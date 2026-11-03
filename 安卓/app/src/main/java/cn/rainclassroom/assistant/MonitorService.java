package cn.rainclassroom.assistant;

import android.app.*;
import android.content.*;
import android.os.*;
import android.speech.tts.TextToSpeech;
import android.text.Html;
import android.util.Base64;
import org.json.*;
import java.io.IOException;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.*;
import java.util.regex.*;
import okhttp3.*;

/** 课堂轮询与 WebSocket 在前台服务中运行；答案由 ReviewRegistry 统一完成。 */
public class MonitorService extends Service {
    public static final String STOP = "cn.rainclassroom.assistant.STOP";
    public static final String UPDATE = "cn.rainclassroom.assistant.UPDATE";
    private static final String SITE = "https://www.yuketang.cn";
    private static final String CHANNEL = "classroom_monitor";
    private static final int SERVICE_NOTIFICATION = 100;
    public static volatile boolean isRunning = false;
    private static volatile String currentCourses = "[]", currentStatus = "{}";
    public static String getStatusJson(){return currentStatus;}
    private volatile String monitorState="未启动";
    private volatile long lastScan,lastMessage,lastQuestion;
    private final java.util.concurrent.atomic.AtomicInteger receivedCount=new java.util.concurrent.atomic.AtomicInteger();
    private HistoryStore history;
    private void connectionStatus(){
        int ready=0,retry=0,connecting=0;
        for(Lesson l:lessons.values()){if("课堂已就绪".equals(l.connectionState))ready++;
            else if(l.connectionState.startsWith("断线"))retry++;else connecting++;}
        status(ready>0?(retry>0?"部分课程断线重连":connecting>0?"部分课程连接中":"监听中"):
            retry>0?"断线重连":connecting>0?"课堂连接中":"暂无在课课程");
    }
    private void status(String state){
        monitorState=state;
        try{currentStatus=new JSONObject().put("state",state).put("lastScan",lastScan).put("lastMessage",lastMessage)
            .put("lastQuestion",lastQuestion).put("received",receivedCount.get()).put("userName",userName==null?"":userName).toString();}catch(JSONException ignored){}
        changed();
        if(running){
            PendingIntent p=PendingIntent.getActivity(this,0,new Intent(this,MainActivity.class),PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);
            Notification n=new Notification.Builder(this,CHANNEL).setSmallIcon(android.R.drawable.ic_dialog_info).setContentTitle("雨课堂助手 · "+state)
                .setContentText("已收 "+receivedCount.get()+" 题 · 点击查看").setContentIntent(p).setOngoing(true).setOnlyAlertOnce(true).build();
            try{((NotificationManager)getSystemService(NOTIFICATION_SERVICE)).notify(SERVICE_NOTIFICATION,n);}catch(SecurityException ignored){}
        }
    }
    public static String getCoursesJson() { return currentCourses; }
    private final OkHttpClient http = new OkHttpClient.Builder().connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(25, TimeUnit.SECONDS).callTimeout(40, TimeUnit.SECONDS).pingInterval(20,TimeUnit.SECONDS)
            .followSslRedirects(false).build();
    private final ConcurrentHashMap<String, Lesson> lessons = new ConcurrentHashMap<>();
    private final ConcurrentHashMap<String, Integer> reviewNotifications = new ConcurrentHashMap<>();
    private final ScheduledExecutorService timers = Executors.newScheduledThreadPool(2);
    private final ExecutorService workers = Executors.newCachedThreadPool();
    private final java.util.concurrent.atomic.AtomicInteger notificationSequence = new java.util.concurrent.atomic.AtomicInteger(200);
    private final Handler speechHandler = new Handler(Looper.getMainLooper());
    private final ArrayDeque<String> pendingSpeech = new ArrayDeque<>();
    // 语音引擎仅在第一次启用且需要播报时初始化；以下状态只在主线程访问。
    private TextToSpeech speech;
    private int speechState; // 0=未初始化, 1=初始化中, 2=可用, 3=不可用, 4=已关闭
    private final SharedPreferences.OnSharedPreferenceChangeListener speechPreferenceListener = (config, key) -> {
        if ("audio_on".equals(key) && !config.getBoolean("audio_on", false)) speechHandler.post(() -> {
            pendingSpeech.clear();
            if (speech != null) try { speech.stop(); } catch (RuntimeException ignored) { }
        });
    };
    private volatile boolean running;
    private android.content.SharedPreferences prefs;
    private String session, userName;
    private Object userId;

    @Override public void onCreate() {
        super.onCreate();
        history=new HistoryStore(this);
        ReviewRegistry.setListener(r->{
            try{history.save(r);}catch(RuntimeException e){log("历史题目保存失败，请检查存储空间");}
            changed();
        });
        prefs = getSharedPreferences("rain_config", MODE_PRIVATE);
        prefs.registerOnSharedPreferenceChangeListener(speechPreferenceListener);
        NotificationManager manager = (NotificationManager)getSystemService(NOTIFICATION_SERVICE);
        manager.createNotificationChannel(new NotificationChannel(CHANNEL, "课堂监听与答案", NotificationManager.IMPORTANCE_DEFAULT));
    }
    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && STOP.equals(intent.getAction())) { stopMonitoring(); stopSelf(); return START_NOT_STICKY; }
        if(intent!=null&&"cn.rainclassroom.assistant.RETRY".equals(intent.getAction())){if(running)retryReview(intent.getStringExtra("review_id"));else stopSelf();return START_NOT_STICKY;}
        if (running) return START_NOT_STICKY;
        Intent open = new Intent(this, MainActivity.class);
        PendingIntent pending = PendingIntent.getActivity(this, 0, open, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        Notification notification = new Notification.Builder(this, CHANNEL).setSmallIcon(android.R.drawable.ic_dialog_info)
                .setContentTitle("雨课堂助手正在监听").setContentText("点击查看课程与待核对答案")
                .setContentIntent(pending).setOngoing(true).setOnlyAlertOnce(true).build();
        startForeground(SERVICE_NOTIFICATION, notification);
        session = prefs.getString("sessionid", "").trim();
        if (session.isEmpty()) { log("请先登录雨课堂，再启动监听"); stopSelf(); return START_NOT_STICKY; }
        running = true; isRunning = true;
        status("正在检测课程");
        log("开始监听课堂");
        timers.scheduleWithFixedDelay(() -> { if (running) poll(); }, 0, 30, TimeUnit.SECONDS);
        return START_NOT_STICKY;
    }
    @Override public void onDestroy() {
        stopMonitoring(); shutdownSpeech();
        prefs.unregisterOnSharedPreferenceChangeListener(speechPreferenceListener);
        ReviewRegistry.setListener(null);
        if(history!=null)history.close();
        super.onDestroy();
    }
    @Override public IBinder onBind(Intent intent) { return null; }
    private synchronized void stopMonitoring() {
        if (!running && !isRunning) return;
        running = false; isRunning = false;
        status("已停止");
        shutdownSpeech();
        ReviewRegistry.cancelAll();
        for (Lesson lesson : lessons.values()) lesson.close();
        lessons.clear();
        http.dispatcher().cancelAll();
        timers.shutdownNow(); workers.shutdownNow();
        ((NotificationManager)getSystemService(NOTIFICATION_SERVICE)).cancelAll();
        log("监听已停止，待答题目已取消");
        courses();
        stopForeground(true);
    }
    private void log(String text) { sendBroadcast(new Intent(UPDATE).setPackage(getPackageName()).putExtra("log", text)); }
    private void courses() {
        JSONArray arr = new JSONArray();
        for (Lesson l : lessons.values()) try {
            arr.put(new JSONObject().put("lessonId", l.id).put("courseName", l.name).put("classroomId", l.classroomId).put("state",l.connectionState));
        } catch (JSONException ignored) { }
        currentCourses = arr.toString();
        sendBroadcast(new Intent(UPDATE).setPackage(getPackageName()).putExtra("courses", currentCourses));
    }
    private void changed() { sendBroadcast(new Intent(UPDATE).setPackage(getPackageName())); }
    private void speak(String message) {
        if (!running || !prefs.getBoolean("audio_on", false)) return;
        speechHandler.post(() -> {
            if (!running || !prefs.getBoolean("audio_on", false) || speechState >= 3) return;
            if (speechState == 0) {
                speechState = 1;
                try {
                    speech = new TextToSpeech(getApplicationContext(), status ->
                            speechHandler.post(() -> speechInitialized(status)));
                } catch (RuntimeException e) { speechState = 3; pendingSpeech.clear(); return; }
            }
            if (speechState == 1) {
                if (pendingSpeech.size() >= 8) pendingSpeech.removeFirst();
                pendingSpeech.addLast(message);
            } else if (speechState == 2) {
                try { speech.speak(message, TextToSpeech.QUEUE_ADD, null, UUID.randomUUID().toString()); }
                catch (RuntimeException ignored) { }
            }
        });
    }
    private void speechInitialized(int status) {
        if (speechState != 1 || speech == null) return;
        try {
            int language = status == TextToSpeech.SUCCESS ? speech.setLanguage(Locale.SIMPLIFIED_CHINESE) : TextToSpeech.LANG_NOT_SUPPORTED;
            if (language == TextToSpeech.LANG_MISSING_DATA || language == TextToSpeech.LANG_NOT_SUPPORTED) {
                speechState = 3; pendingSpeech.clear(); return;
            }
            speechState = 2;
            while (running && prefs.getBoolean("audio_on", false) && !pendingSpeech.isEmpty()) {
                speech.speak(pendingSpeech.removeFirst(), TextToSpeech.QUEUE_ADD, null, UUID.randomUUID().toString());
            }
            pendingSpeech.clear();
        } catch (RuntimeException e) { speechState = 3; pendingSpeech.clear(); }
    }
    private void shutdownSpeech() {
        // 生命周期回调在主线程；所有异步播报都检查 running 和 speechState。
        speechState = 4; pendingSpeech.clear();
        if (speech != null) { try { speech.stop(); speech.shutdown(); } catch (RuntimeException ignored) { } speech = null; }
    }
    private Headers headers(String authorization) {
        Headers.Builder h = new Headers.Builder().add("Cookie", "sessionid=" + session)
                .add("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36");
        if (authorization != null) h.add("Authorization", "Bearer " + authorization);
        return h.build();
    }
    private static final MediaType JSON = MediaType.get("application/json; charset=utf-8");
    private JSONObject callJson(Request request, long timeoutMs) throws Exception {
        OkHttpClient client = timeoutMs > 0 ? http.newBuilder().callTimeout(timeoutMs, TimeUnit.MILLISECONDS).build() : http;
        try (Response response = client.newCall(request).execute()) {
            if (!response.isSuccessful()) throw new HttpStatus(response.code());
            if (response.body() == null) throw new IOException("服务器响应为空");
            return new JSONObject(response.body().string());
        }
    }
    private static class HttpStatus extends IOException { final int code; HttpStatus(int code) { super("HTTP " + code); this.code = code; } }
    private Request.Builder rainRequest(String path, String authorization) {
        return new Request.Builder().url(SITE + path).headers(headers(authorization));
    }
    private static RequestBody body(JSONObject obj) { return RequestBody.create(obj.toString(), JSON); }
    private void poll() {
        try {
            if(!session.equals(prefs.getString("sessionid",""))){log("账号已切换，旧账号监听已停止");stopMonitoring();stopSelf();return;}
            if (userId == null) {
                JSONObject info = callJson(rainRequest("/api/v3/user/basic-info", null).get().build(), 0);
                if (info.optInt("code", -1) != 0) { prefs.edit().putBoolean("account_verified",false).apply();status("登录已失效");log("登录状态已失效，请重新登录");return; }
                if(!running||!session.equals(prefs.getString("sessionid","")))return;
                JSONObject user = info.getJSONObject("data"); userId = user.get("id"); userName = user.optString("name", "");
                prefs.edit().putString("user_id",String.valueOf(userId)).putString("user_name",userName).putBoolean("account_verified",true).putLong("account_verified_at",System.currentTimeMillis()).apply();
            }
            JSONObject response = callJson(rainRequest("/api/v3/classroom/on-lesson", null).get().build(), 0);
            if(!running||!session.equals(prefs.getString("sessionid","")))return;
            if (response.optInt("code", -1) != 0) { prefs.edit().putBoolean("account_verified",false).apply();status("登录状态待确认");log("获取课程失败，请检查登录状态");return; }
            lastScan=System.currentTimeMillis();
            JSONArray arr = response.getJSONObject("data").getJSONArray("onLessonClassrooms");
            Set<String> active = new HashSet<>();
            for (int i = 0; i < arr.length(); i++) {
                JSONObject item = arr.getJSONObject(i);
                String id = item.getString("lessonId"); active.add(id);
                if (!lessons.containsKey(id)) {
                    Lesson l = new Lesson(id, item.optString("courseName", "课堂"), item.optString("classroomId", ""));
                    if (lessons.putIfAbsent(id, l) == null) {
                        log("检测到课程「" + l.name + "」，正在加入监听"); courses(); workers.execute(l::connect);
                    }
                }
            }
            // 课堂列表可能瞬时为空，不据此关闭已有连接；lessonfinished 明确结束后关闭。
            connectionStatus();
        } catch (Exception e) { if (running) {status("课程检测异常，将重试");log("获取课堂失败，请检查网络后自动重试（" + safeMessage(e) + "）");} }
    }
    private static String safeMessage(Exception e) {
        if (e instanceof VisibleFailure) return e.getMessage();
        if (e instanceof HttpStatus) return e.getMessage();
        if (e instanceof java.net.SocketTimeoutException || e instanceof java.io.InterruptedIOException) return "连接超时";
        return "网络或响应异常";
    }
    private void notifyReview(ReviewRegistry.Review review) {
        int number = notificationSequence.incrementAndGet(); reviewNotifications.put(review.id, number);
        Intent open = new Intent(this, ReviewActivity.class).putExtra("review_id", review.id);
        open.setAction("review:" + review.id);
        PendingIntent pending = PendingIntent.getActivity(this, number, open, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        Notification n = new Notification.Builder(this, CHANNEL).setSmallIcon(android.R.drawable.ic_dialog_info)
                .setContentTitle(review.course + " · 收到新题目").setContentText("点击即可选择或填写，模型在后台生成建议")
                .setContentIntent(pending).setAutoCancel(true).build();
        try { ((NotificationManager)getSystemService(NOTIFICATION_SERVICE)).notify(number, n); }
        catch (SecurityException ignored) { log("通知权限未开启，请在应用内查看待答题目"); }
    }
    private void notifyCall(String title, String message) {
        Intent open = new Intent(this, MainActivity.class);
        PendingIntent p = PendingIntent.getActivity(this, 1, open, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        try { ((NotificationManager)getSystemService(NOTIFICATION_SERVICE)).notify(notificationSequence.incrementAndGet(),
                new Notification.Builder(this, CHANNEL).setSmallIcon(android.R.drawable.ic_dialog_info)
                        .setContentTitle(title).setContentText(message).setContentIntent(p).setAutoCancel(true).build()); }
        catch (SecurityException ignored) { }
    }

    private final class Lesson {
        final String id, name, classroomId;
        final ConcurrentHashMap<String, JSONObject> problems = new ConcurrentHashMap<>();
        final Set<String> dispatched = ConcurrentHashMap.newKeySet();
        final Set<String> announcedProblems = ConcurrentHashMap.newKeySet();
        final Set<String> reviewIds = ConcurrentHashMap.newKeySet();
        final Map<String, ArrayDeque<Long>> danmuTimes = new HashMap<>();
        final Map<String, Long> sentDanmu = new HashMap<>();
        final ExecutorService serial = Executors.newSingleThreadExecutor();
        volatile WebSocket socket;
        volatile String auth;
        volatile boolean closed;
        volatile String connectionState="连接中";
        final java.util.concurrent.atomic.AtomicBoolean reconnectScheduled=new java.util.concurrent.atomic.AtomicBoolean();
        volatile String latestPresentation="";
        boolean cacheAlerted;
        Lesson(String id, String name, String classroomId) { this.id=id; this.name=name; this.classroomId=classroomId; }
        void connect() {
            if (!running || closed) return;
            reconnectScheduled.set(false);connectionState="连接中";courses();connectionStatus();
            try {
                JSONObject data = new JSONObject().put("source", 5).put("lessonId", id);
                Request request = rainRequest("/api/v3/lesson/checkin", null).post(body(data)).build();
                String token;
                try (Response r = http.newCall(request).execute()) {
                    if (!r.isSuccessful() || r.body() == null) throw new HttpStatus(r.code());
                    auth = r.header("Set-Auth");
                    JSONObject parsed = new JSONObject(r.body().string());
                    token = parsed.getJSONObject("data").getString("lessonToken");
                    if (auth == null || auth.isEmpty()) throw new IOException("签到未返回认证信息");
                }
                if (!running || closed) return;
                socket = http.newWebSocket(new Request.Builder().url("wss://www.yuketang.cn/wsapp/").headers(headers(auth)).build(),
                        new WebSocketListener() {
                    @Override public void onOpen(WebSocket ws, Response r) {
                        connectionState="已连接，正在初始化";courses();connectionStatus();
                        try { ws.send(new JSONObject().put("op", "hello").put("userid", userId).put("role", "student")
                                .put("auth", token).put("lessonid", id).toString()); }
                        catch (JSONException e) { log(name + "：无法初始化监听"); }
                    }
                    @Override public void onMessage(WebSocket ws, String text) {
                        long received = SystemClock.elapsedRealtime();
                        lastMessage=System.currentTimeMillis();connectionStatus();
                        if (running && !closed) try { serial.execute(() -> handle(ws, text, received)); } catch (RejectedExecutionException ignored) { }
                    }
                    @Override public void onFailure(WebSocket ws, Throwable t, Response r) { reconnect(); }
                    @Override public void onClosed(WebSocket ws, int code, String reason) { reconnect(); }
                });
                log("「" + name + "」已签到，正在建立课堂连接");
            } catch (Exception e) { if (running && !closed) { log(name + " 连接失败，将重试（" + safeMessage(e) + "）"); reconnect(); } }
        }
        void reconnect() {
            if (running && !closed && reconnectScheduled.compareAndSet(false,true)) {
                connectionState="断线重连（5 秒后）";courses();connectionStatus();log(name+"：课堂连接断开，5 秒后重试");
                try { timers.schedule(this::connect,5,TimeUnit.SECONDS); } catch(RejectedExecutionException ignored){}
            }
        }
        void close() {
            closed = true;connectionState="已停止";
            for (String reviewId : reviewIds) ReviewRegistry.cancel(reviewId);
            if (socket != null) socket.cancel(); serial.shutdownNow();
        }
        void cache(String presentation) {
            if (presentation == null || presentation.isEmpty()) return;
            latestPresentation=presentation;
            try {
                String encoded = java.net.URLEncoder.encode(presentation, "UTF-8");
                JSONObject result = callJson(rainRequest("/api/v3/lesson/presentation/fetch?presentation_id=" + encoded, auth).get().build(), 0);
                JSONArray slides = result.getJSONObject("data").getJSONArray("slides");
                for (int i=0;i<slides.length();i++) {
                    JSONObject p = slides.getJSONObject(i).optJSONObject("problem");
                    if (p != null && p.has("problemId")) problems.put(p.getString("problemId"), p);
                }
            } catch (Exception e) {
                if (running && !closed) {
                    log(name + "：无法获取课件题面，请在雨课堂查看（" + safeMessage(e) + "）");
                    if (!cacheAlerted) { cacheAlerted = true; notifyCall(name + " · 课件异常", "无法获取课件，遇到题目时请在雨课堂查看"); }
                }
            }
        }
        void handle(WebSocket ws, String raw, long received) {
            try {
                JSONObject data = new JSONObject(raw); String op = data.optString("op");
                if ("hello".equals(op)) {
                    connectionState="课堂已就绪";courses();connectionStatus();
                    LinkedHashSet<String> presentations = new LinkedHashSet<>();
                    JSONArray timeline = data.optJSONArray("timeline");
                    if (timeline != null) for (int i=0;i<timeline.length();i++) {
                        JSONObject slide = timeline.optJSONObject(i);
                        if (slide != null && "slide".equals(slide.optString("type"))) presentations.add(slide.optString("pres"));
                    }
                    presentations.add(data.optString("presentation"));
                    latestPresentation=data.optString("presentation",latestPresentation);
                    for (String p : presentations) if(p!=null&&!p.isEmpty()) workers.execute(()->cache(p));
                    JSONArray unlocked = data.optJSONArray("unlockedproblem");
                    if (unlocked != null) for (int i=0;i<unlocked.length();i++) {
                        ws.send(new JSONObject().put("op", "probleminfo").put("lessonid", id)
                                .put("problemid", unlocked.optString(i)).put("msgid", 1).toString());
                    }
                } else if ("unlockproblem".equals(op)) {
                    JSONObject p = data.optJSONObject("problem");
                    if(p==null)p=data;
                    {
                        long limit = p.optLong("limit", -2);
                        long deadline = limit == -1 ? -1 : received + limit * 1000L;
                        if(limit==-1||limit>0)startProblem(p.optString("sid",p.optString("problemId",p.optString("problemid",p.optString("prob")))),deadline,p);
                        else startProblem(p.optString("sid",p.optString("problemId",p.optString("problemid",p.optString("prob")))),-2,p);
                    }
                } else if ("probleminfo".equals(op)) {
                    long limit = data.optLong("limit", -2);
                    if (limit == -1) startProblem(data.optString("problemid"), -1, data);
                    else if (limit > 0 && data.has("now") && data.has("dt")) {
                        long remainMs = limit*1000L - (data.getLong("now") - data.getLong("dt"));
                        if (remainMs > 0) startProblem(data.optString("problemid"), received + remainMs, data);
                        else startProblem(data.optString("problemid"),received+remainMs,data);
                    } else startProblem(data.optString("problemid"),-2,data);
                } else if ("presentationupdated".equals(op) || "presentationcreated".equals(op)) workers.execute(()->cache(data.optString("presentation")));
                else if ("lessonfinished".equals(op)) {
                    closed = true; if (socket != null) socket.close(1000, "课程结束");
                    for (String reviewId : reviewIds) ReviewRegistry.cancel(reviewId);
                    lessons.remove(id, this); log(name + "：课程已结束"); courses();connectionStatus();serial.shutdown();
                } else if ("callpaused".equals(op)) {
                    String called = data.optString("name", "同学"); String message = called.equals(userName) ? "老师点到了你" : "老师点到了 " + called;
                    log(name + "：" + message); notifyCall(name + " · 点名", message);
                    if (called.equals(userName)) speak(name + "，老师点到你了");
                } else if ("newdanmu".equals(op) && prefs.getBoolean("auto_danmu", false)) {
                    followDanmu(data.optString("danmu"));
                }
            } catch (Exception e) { if (running && !closed) log(name + "：课堂消息处理失败（" + safeMessage(e) + "）"); }
        }
        void followDanmu(String text) throws Exception {
            text = text.trim(); if (text.isEmpty()) return;
            String key = text.toLowerCase(Locale.ROOT); long now = SystemClock.elapsedRealtime();
            ArrayDeque<Long> times = danmuTimes.computeIfAbsent(key, k -> new ArrayDeque<>());
            while (!times.isEmpty() && now - times.peekFirst() > 60000) times.removeFirst();
            times.addLast(now);
            int limit = Math.max(1, prefs.getInt("danmu_limit", 5));
            if (times.size() < limit || now - sentDanmu.getOrDefault(key, -60000L) < 60000) return;
            times.clear(); sentDanmu.put(key, now);
            final String content = text;
            workers.execute(() -> {
                try {
                    JSONObject data = new JSONObject().put("extra", "").put("fromStart", "50").put("lessonId", id)
                            .put("message", content).put("requiredCensor", false).put("showStatus", true)
                            .put("target", "").put("userName", "").put("wordCloud", true);
                    JSONObject result = callJson(rainRequest("/api/v3/lesson/danmu/send", auth).post(body(data)).build(), 0);
                    log(name + (result.optInt("code", -1) == 0 ? "：已跟随发送弹幕" : "：自动弹幕发送失败"));
                } catch (Exception e) { if (running) log(name + "：自动弹幕发送结果未确认"); }
            });
        }
        void startProblem(String problemId,long questionDeadline,JSONObject pushed) {
            if(!running||closed)return;
            boolean missingIdentity=problemId==null||problemId.isEmpty();
            if(missingIdentity)problemId="unknown-"+Integer.toHexString(String.valueOf(pushed).hashCode());
            if(!dispatched.add(problemId)){
                JSONObject incoming=mergeProblem(problems.get(problemId),pushed);
                for(ReviewRegistry.Review current:ReviewRegistry.list())if(current.lessonId.equals(id)&&current.problemId.equals(problemId)){
                    boolean timingWasUnknown=current.questionDeadlineElapsed==-2;
                    if(questionDeadline!=-2){current.questionDeadlineElapsed=questionDeadline;scheduleExpiry(current);}
                    if(timingWasUnknown||"题面待补全".equals(current.modelState)||"题面获取失败".equals(current.modelState)){
                        synchronized(current.problem){for(Iterator<String> it=incoming.keys();it.hasNext();){String k=it.next();try{current.problem.put(k,incoming.opt(k));}catch(JSONException ignored){}}}
                        current.type=incoming.optInt("problemType",current.type);
                        if(hasContent(current.problem)){ReviewRegistry.modelState(current,"未请求","已补充题目信息，可手动作答");beginModel(current);}
                    }
                }
                return;
            }
            final String pid=problemId;
            JSONObject cached=problems.get(pid),problem=mergeProblem(cached,pushed);
            int type=problem.optInt("problemType",-1);
            if(missingIdentity)try{problem.put("_identity_missing",true);}catch(JSONException ignored){}
            String account=userId==null?prefs.getString("user_id",""):String.valueOf(userId);
            JSONObject stored;
            try{stored=history.get(account,id,pid);}
            catch(RuntimeException e){
                lastQuestion=System.currentTimeMillis();receivedCount.incrementAndGet();status(monitorState);
                log(name+"：已收到题目，但无法读取提交历史；为避免重复提交，本题暂停处理，请在雨课堂核对");
                notifyCall(name+" · 历史记录读取异常","本题不会自动提交，请在雨课堂核对");return;
            }
            ReviewRegistry.Review review=ReviewRegistry.receivePending(account,id,name,pid,type,problem,questionDeadline,stored,(r,cancelled)->{
                reviewIds.remove(r.id);Integer n=reviewNotifications.remove(r.id);
                if(n!=null)((NotificationManager)getSystemService(NOTIFICATION_SERVICE)).cancel(n);changed();
                if(cancelled||!running||closed)return;
                try{workers.execute(()->submitAnswer(r));}catch(RejectedExecutionException e){ReviewRegistry.submission(r,"提交结果未知");}
            });
            if(review==null){
                lastQuestion=System.currentTimeMillis();receivedCount.incrementAndGet();status(monitorState);
                String priorState=stored.optString("state");
                log(name+"：已收到曾处理的题目（"+priorState+"），仅保留历史查看，不会重新请求模型或提交");
                notifyCall(name+" · 历史题目",priorState+"；请在历史题目查看，必要时在雨课堂核对");return;
            }
            reviewIds.add(review.id);lastQuestion=System.currentTimeMillis();receivedCount.incrementAndGet();status(monitorState);
            notifyReview(review);speak(name+"，收到新题目");
            if(questionDeadline==-2){
                ReviewRegistry.modelState(review,"未请求","已收到题目，但时限信息缺失，待补全后才可提交");
                if(socket!=null)try{socket.send(new JSONObject().put("op","probleminfo").put("lessonid",id).put("problemid",pid).put("msgid",1).toString());}catch(JSONException ignored){}
            }else if(questionDeadline!=-1&&questionDeadline<=SystemClock.elapsedRealtime()){
                ReviewRegistry.expire(review.id);log(name+"：已收到题目，但题目已过期");return;
            }else scheduleExpiry(review);
            if(missingIdentity){ReviewRegistry.modelState(review,"题面获取失败","题目通知缺少编号，暂不能在助手提交，请在雨课堂查看");return;}
            if(cached!=null&&cached.has("result")&&!cached.isNull("result")){
                ReviewRegistry.modelState(review,"未请求","服务端标记本题已作答");ReviewRegistry.submission(review,"已作答");ReviewRegistry.cancel(review.id);ReviewRegistry.submission(review,"已作答");return;
            }
            if(type==-1||!hasContent(problem)||((type==1||type==2)&&(problem.optJSONArray("options")==null||problem.optJSONArray("options").length()==0))){
                ReviewRegistry.modelState(review,"题面待补全","已收到题目，正在重新获取题面");
                if(socket!=null)try{socket.send(new JSONObject().put("op","probleminfo").put("lessonid",id).put("problemid",pid).put("msgid",1).toString());}catch(JSONException ignored){}
                try{workers.execute(()->{
                    cache(latestPresentation);
                    JSONObject full=mergeProblem(problems.get(pid),pushed);
                    review.type=full.optInt("problemType",review.type);
                    synchronized(problem){for(Iterator<String> it=full.keys();it.hasNext();){String k=it.next();try{problem.put(k,full.opt(k));}catch(JSONException ignored){}}}
                    if(!hasContent(full)||((review.type==1||review.type==2)&&(full.optJSONArray("options")==null||full.optJSONArray("options").length()==0))){
                        ReviewRegistry.modelState(review,"题面获取失败","已收到题目，但题面或选项不完整；可重新获取题面");return;
                    }
                    ReviewRegistry.modelState(review,"未请求","题面已补全，可重新打开题目作答");changed();
                    beginModel(review);
                });}catch(RejectedExecutionException ignored){}
                return;
            }
            beginModel(review);
        }
        void scheduleExpiry(ReviewRegistry.Review review){
            long d=review.questionDeadlineElapsed;if(d==-1||d==-2)return;
            try{timers.schedule(()->{
                long current=review.questionDeadlineElapsed;
                if(current!=-1&&current!=-2&&SystemClock.elapsedRealtime()>=current)ReviewRegistry.expire(review.id);
            },Math.max(0,d-SystemClock.elapsedRealtime()),TimeUnit.MILLISECONDS);}catch(RejectedExecutionException ignored){}
        }
        void beginModel(ReviewRegistry.Review review){
            if(review.ended()||!running||closed)return;
            if(review.questionDeadlineElapsed==-2){ReviewRegistry.modelState(review,"未请求","题目时限待确认，暂不能提交，可先选择答案");return;}
            if(review.type!=1&&review.type!=2&&review.type!=4&&review.type!=5){ReviewRegistry.modelState(review,"未请求","题型暂不支持，请在雨课堂查看");return;}
            if(!prefs.getBoolean("auto_answer",true)){ReviewRegistry.modelState(review,"未请求","模型已关闭，可立即手动作答");return;}
            if(!ReviewRegistry.beginModel(review))return;
            try{workers.execute(()->answer(review));}catch(RejectedExecutionException ignored){}
        }
        void answer(ReviewRegistry.Review review){
            if(review.ended()||!running||closed)return;
            long configured=Math.max(5,Math.min(600,prefs.getInt("timeout",30)))*1000L;
            long deadline=SystemClock.elapsedRealtime()+configured;
            if(review.questionDeadlineElapsed!=-1)deadline=Math.min(deadline,review.questionDeadlineElapsed-5000);
            final long totalDeadline=deadline;
            ScheduledFuture<?> watchdog=null;
            try{
                watchdog=timers.schedule(()->{
                    if("处理中".equals(review.modelState)||"重试中".equals(review.modelState))
                        ReviewRegistry.modelState(review,"超时","模型已超过总等待上限，仍可手动作答");
                },Math.max(0,deadline-SystemClock.elapsedRealtime()),TimeUnit.MILLISECONDS);
                JSONArray content=questionContent(review.problem,review.type,deadline);
                JSONArray answers=ModelClient.complete(http,prefs.getString("base_url",""),prefs.getString("api_key",""),prefs.getString("model",""),
                    content,review.type,review.problem,deadline,()->running&&!closed&&!review.ended(),message->ReviewRegistry.modelState(review,"重试中",message));
                if(!running||closed||review.ended()||SystemClock.elapsedRealtime()>=totalDeadline)return;
                long now=SystemClock.elapsedRealtime();
                long reviewMs=Math.max(1,Math.min(300,prefs.getInt("review_seconds",15)))*1000L;
                if(review.questionDeadlineElapsed!=-1)reviewMs=Math.min(reviewMs,Math.max(0,review.questionDeadlineElapsed-now-5000));
                if(ReviewRegistry.applyModel(review.id,answers,now+reviewMs,prefs.getBoolean("auto_submit",true))&&review.autoSubmit){
                    timers.schedule(()->ReviewRegistry.autoSubmit(review.id),Math.max(0,review.deadlineElapsed-SystemClock.elapsedRealtime()),TimeUnit.MILLISECONDS);
                }
                changed();
            }catch(Exception e){
                if(running&&!closed&&!review.ended()){
                    String state=e instanceof ModelClient.Failure?((ModelClient.Failure)e).state:
                        e instanceof java.io.InterruptedIOException||SystemClock.elapsedRealtime()>=totalDeadline?"超时":"失败";
                    String message=e instanceof ModelClient.Failure?e.getMessage():safeMessage(e);
                    ReviewRegistry.modelState(review,state,message+"；仍可手动作答");
                }
            }finally{if(watchdog!=null)watchdog.cancel(false);}
        }
        void manual(String message) { log(name + "：" + message); notifyCall(name + " · 请手动回答", message); }
        void submitAnswer(ReviewRegistry.Review review) {
            if (!running || closed) {ReviewRegistry.submission(review,"已取消");return;}
            long remaining = review.questionDeadlineElapsed == -1 ? 10000 : review.questionDeadlineElapsed - SystemClock.elapsedRealtime();
            if (remaining <= 0) { ReviewRegistry.submission(review,"已过期");log(name + "：题目已超时，未提交");speak(name+"，题目已超时");return; }
            try {
                JSONObject payload = new JSONObject().put("problemId", review.problemId).put("problemType", review.type)
                        .put("dt", System.currentTimeMillis()/1000L).put("result", review.answers);
                JSONObject result = callJson(rainRequest("/api/v3/lesson/problem/answer", auth).post(body(payload)).build(), Math.min(10000, remaining));
                if (result.optInt("code", -1) == 0) { ReviewRegistry.submission(review,"提交成功");log(name+"：答案提交成功");speak(name+"，答案提交成功"); }
                else { ReviewRegistry.submission(review,"提交失败");log(name+"：答案提交失败，请在雨课堂查看");speak(name+"，答案提交失败"); }
            } catch (Exception e) {
                ReviewRegistry.submission(review,"提交结果未知");if(running){log(name+"：提交结果未确认，请在雨课堂核对；不会重复提交");speak(name+"，提交结果未确认");}
            }
        }
    }

    @Override public void onTaskRemoved(Intent rootIntent){status(running?monitorState:"已停止");super.onTaskRemoved(rootIntent);}
    private void retryReview(String reviewId){
        ReviewRegistry.Review review=ReviewRegistry.get(reviewId);if(review==null)return;
        Lesson lesson=lessons.get(review.lessonId);if(lesson==null)return;
        if("处理中".equals(review.modelState)||"重试中".equals(review.modelState))return;
        workers.execute(()->{
            if("题面获取失败".equals(review.modelState)||"题面待补全".equals(review.modelState)){
                lesson.cache(lesson.latestPresentation);JSONObject full=mergeProblem(lesson.problems.get(review.problemId),review.problem);
                review.type=full.optInt("problemType",review.type);
                synchronized(review.problem){for(Iterator<String> it=full.keys();it.hasNext();){String k=it.next();try{review.problem.put(k,full.opt(k));}catch(JSONException ignored){}}}
                ReviewRegistry.modelState(review,hasContent(full)?"未请求":"题面获取失败",hasContent(full)?"题面已更新，请重新打开题目":"仍未获取到题面，请在雨课堂核对");
            }else lesson.beginModel(review);
        });
    }
    private static JSONObject mergeProblem(JSONObject cache, JSONObject pushed) {
        JSONObject out = new JSONObject();
        String[] keys = {"problemType", "body", "content", "title", "options", "blanks", "images"};
        for (String key : keys) {
            Object v = pushed == null ? null : pushed.opt(key);
            if (v == null || v == JSONObject.NULL || "".equals(v)) v = cache == null ? null : cache.opt(key);
            if (v != null && v != JSONObject.NULL) try { out.put(key, v); } catch (JSONException ignored) { }
        }
        if (!out.has("problemType")) {
            Object type = pushed == null ? null : pushed.opt("type");
            if (!(type instanceof Number) && cache != null) type = cache.opt("type");
            if (type != null && type != JSONObject.NULL) try { out.put("problemType", type); } catch (JSONException ignored) { }
        }
        return out;
    }
    private static boolean hasContent(JSONObject p) {
        return !p.optString("body", "").trim().isEmpty() || !p.optString("content", "").trim().isEmpty()
                || !p.optString("title", "").trim().isEmpty() || p.optJSONArray("options") != null || p.optJSONArray("images") != null;
    }
    private static class VisibleFailure extends Exception { VisibleFailure(String message) { super(message); } }
    private static final Pattern IMAGE_TAG = Pattern.compile("<img\\b[^>]*>", Pattern.CASE_INSENSITIVE);
    private static final Pattern IMAGE_ATTR = Pattern.compile("(?:^|\\s)(data-src|src)\\s*=\\s*(?:\"([^\"]*)\"|'([^']*)'|([^\\s>]+))", Pattern.CASE_INSENSITIVE);
    private static String plain(String html) { return Html.fromHtml(html, Html.FROM_HTML_MODE_LEGACY).toString().replace("\uFFFC", "").trim(); }
    private static String firstText(JSONObject p, String... keys) {
        for (String key : keys) { String s=p.optString(key, ""); if (!s.isEmpty() && !"null".equals(s)) return s; }
        return "";
    }
    private static void collectHtmlImages(String html, String label, List<String[]> images) {
        Matcher tags = IMAGE_TAG.matcher(html);
        while (tags.find()) {
            Matcher attrs = IMAGE_ATTR.matcher(tags.group()); String src=null, dataSrc=null;
            while (attrs.find()) {
                String value=attrs.group(2)!=null ? attrs.group(2) : attrs.group(3)!=null ? attrs.group(3) : attrs.group(4);
                if ("data-src".equalsIgnoreCase(attrs.group(1))) dataSrc=value; else src=value;
            }
            String value = dataSrc != null && !dataSrc.isEmpty() ? dataSrc : src;
            if (value != null && !value.isEmpty()) images.add(new String[]{label, plain(value)});
        }
    }
    private static void collectImages(Object value, String label, List<String[]> images) {
        if (value == null || value == JSONObject.NULL) return;
        if (value instanceof JSONArray) {
            JSONArray list = (JSONArray)value;
            for (int i=0;i<list.length();i++) collectImages(list.opt(i), label, images);
        } else if (value instanceof JSONObject) {
            String url = ((JSONObject)value).optString("url", ""); if (!url.isEmpty()) images.add(new String[]{label, url});
        } else if (value instanceof String && !((String)value).isEmpty()) images.add(new String[]{label, (String)value});
    }
    private String imageData(String src, long deadline) throws Exception {
        if (src.startsWith("data:image/")) {
            if (src.length() > 12_000_000) throw new VisibleFailure("题目图片过大");
            return src;
        }
        URL url = new URL(new URL(SITE + "/"), src);
        if (!"https".equalsIgnoreCase(url.getProtocol()) || url.getUserInfo() != null) throw new VisibleFailure("题目图片地址不安全");
        long remaining = deadline - SystemClock.elapsedRealtime();
        if (remaining <= 0) throw new VisibleFailure("答题时间不足");
        OkHttpClient client = http.newBuilder().callTimeout(Math.min(15000, remaining), TimeUnit.MILLISECONDS).build();
        try (Response response = client.newCall(new Request.Builder().url(url).get().build()).execute()) {
            if (!response.isSuccessful() || response.body() == null) throw new VisibleFailure("题目图片加载失败");
            ResponseBody b = response.body();
            if (b.contentLength() > 8_000_000) throw new VisibleFailure("题目图片过大");
            MediaType media = b.contentType();
            if (media == null || !"image".equalsIgnoreCase(media.type())) throw new VisibleFailure("题目图片格式不正确");
            java.io.ByteArrayOutputStream buffer = new java.io.ByteArrayOutputStream();
            byte[] chunk = new byte[16384]; int count;
            java.io.InputStream stream = b.byteStream();
            while ((count = stream.read(chunk)) != -1) {
                if (buffer.size() + count > 8_000_000) throw new VisibleFailure("题目图片过大");
                buffer.write(chunk, 0, count);
            }
            byte[] bytes = buffer.toByteArray();
            if (bytes.length == 0) throw new VisibleFailure("题目图片为空");
            return "data:" + media.type() + "/" + media.subtype() + ";base64," + Base64.encodeToString(bytes, Base64.NO_WRAP);
        }
    }
    private JSONArray questionContent(JSONObject problem, int type, long deadline) throws Exception {
        List<String[]> images = new ArrayList<>();
        String html = firstText(problem, "body", "content", "title");
        StringBuilder text = new StringBuilder("题型：").append(type == 1 ? "单选题" : type == 2 ? "多选题" : type == 4 ? "填空题" : "简答题")
                .append("\n题目：").append(plain(html));
        collectHtmlImages(html, "题干图片", images); collectImages(problem.opt("images"), "题干图片", images);
        if (plain(html).isEmpty() && images.isEmpty()) throw new VisibleFailure("未获取到题干");
        JSONArray options = problem.optJSONArray("options");
        if (options != null) for (int i=0;i<options.length();i++) {
            Object option = options.opt(i); String key = String.valueOf((char)('A'+i)); String value;
            if (option instanceof JSONObject) {
                JSONObject o = (JSONObject)option; key = o.optString("key", key); value = firstText(o, "value", "content");
                collectImages(o.opt("images"), "选项"+key+"图片", images);
            } else value = String.valueOf(option);
            text.append("\n").append(key).append(". ").append(plain(value));
            collectHtmlImages(value, "选项"+key+"图片", images);
        }
        if (type == 4) { JSONArray blanks=problem.optJSONArray("blanks"); text.append("\n空数：").append(blanks == null || blanks.length()==0 ? 1 : blanks.length()); }
        JSONArray content = new JSONArray().put(new JSONObject().put("type", "text").put("text", text.toString()));
        Set<String> seen = new HashSet<>();
        for (String[] image : images) if (seen.add(image[0] + "\n" + image[1])) {
            String base64 = imageData(image[1], deadline);
            content.put(new JSONObject().put("type", "text").put("text", image[0]));
            content.put(new JSONObject().put("type", "image_url").put("image_url", new JSONObject().put("url", base64)));
        }
        return content;
    }
}
