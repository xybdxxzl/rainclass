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
    private static volatile String currentCourses = "[]";
    public static String getCoursesJson() { return currentCourses; }
    private final OkHttpClient http = new OkHttpClient.Builder().connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(25, TimeUnit.SECONDS).callTimeout(40, TimeUnit.SECONDS)
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
        prefs = getSharedPreferences("rain_config", MODE_PRIVATE);
        prefs.registerOnSharedPreferenceChangeListener(speechPreferenceListener);
        NotificationManager manager = (NotificationManager)getSystemService(NOTIFICATION_SERVICE);
        manager.createNotificationChannel(new NotificationChannel(CHANNEL, "课堂监听与答案", NotificationManager.IMPORTANCE_DEFAULT));
    }
    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && STOP.equals(intent.getAction())) { stopMonitoring(); stopSelf(); return START_NOT_STICKY; }
        if (running) return START_NOT_STICKY;
        Intent open = new Intent(this, MainActivity.class);
        PendingIntent pending = PendingIntent.getActivity(this, 0, open, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        Notification notification = new Notification.Builder(this, CHANNEL).setSmallIcon(android.R.drawable.ic_dialog_info)
                .setContentTitle("雨课堂助手正在监听").setContentText("点击查看课程与待核对答案")
                .setContentIntent(pending).setOngoing(true).build();
        startForeground(SERVICE_NOTIFICATION, notification);
        session = prefs.getString("sessionid", "").trim();
        if (session.isEmpty()) { log("请先登录雨课堂，再启动监听"); stopSelf(); return START_NOT_STICKY; }
        running = true; isRunning = true;
        log("开始监听课堂");
        timers.scheduleWithFixedDelay(() -> { if (running) poll(); }, 0, 30, TimeUnit.SECONDS);
        return START_NOT_STICKY;
    }
    @Override public void onDestroy() {
        stopMonitoring(); shutdownSpeech();
        prefs.unregisterOnSharedPreferenceChangeListener(speechPreferenceListener);
        super.onDestroy();
    }
    @Override public IBinder onBind(Intent intent) { return null; }
    private synchronized void stopMonitoring() {
        if (!running && !isRunning) return;
        running = false; isRunning = false;
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
            arr.put(new JSONObject().put("lessonId", l.id).put("courseName", l.name).put("classroomId", l.classroomId));
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
            if (userId == null) {
                JSONObject info = callJson(rainRequest("/api/v3/user/basic-info", null).get().build(), 0);
                if (info.optInt("code", -1) != 0) { log("登录状态已失效，请重新登录"); return; }
                JSONObject user = info.getJSONObject("data"); userId = user.get("id"); userName = user.optString("name", "");
            }
            JSONObject response = callJson(rainRequest("/api/v3/classroom/on-lesson", null).get().build(), 0);
            if (response.optInt("code", -1) != 0) { log("获取课程失败，请检查登录状态"); return; }
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
        } catch (Exception e) { if (running) log("获取课堂失败，请检查网络后自动重试（" + safeMessage(e) + "）"); }
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
                .setContentTitle(review.course + " · 答案待确认").setContentText("点击查看和修改，倒计时结束自动提交")
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
        boolean cacheAlerted;
        Lesson(String id, String name, String classroomId) { this.id=id; this.name=name; this.classroomId=classroomId; }
        void connect() {
            if (!running || closed) return;
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
                        try { ws.send(new JSONObject().put("op", "hello").put("userid", userId).put("role", "student")
                                .put("auth", token).put("lessonid", id).toString()); }
                        catch (JSONException e) { log(name + "：无法初始化监听"); }
                    }
                    @Override public void onMessage(WebSocket ws, String text) {
                        long received = SystemClock.elapsedRealtime();
                        if (running && !closed) try { serial.execute(() -> handle(ws, text, received)); } catch (RejectedExecutionException ignored) { }
                    }
                    @Override public void onFailure(WebSocket ws, Throwable t, Response r) { reconnect(); }
                    @Override public void onClosed(WebSocket ws, int code, String reason) { reconnect(); }
                });
                log("「" + name + "」已签到并连接课堂");
            } catch (Exception e) { if (running && !closed) { log(name + " 连接失败，将重试（" + safeMessage(e) + "）"); reconnect(); } }
        }
        void reconnect() {
            if (running && !closed) try { timers.schedule(this::connect, 5, TimeUnit.SECONDS); } catch (RejectedExecutionException ignored) { }
        }
        void close() {
            closed = true;
            for (String reviewId : reviewIds) ReviewRegistry.cancel(reviewId);
            if (socket != null) socket.cancel(); serial.shutdownNow();
        }
        void cache(String presentation) {
            if (presentation == null || presentation.isEmpty()) return;
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
                    LinkedHashSet<String> presentations = new LinkedHashSet<>();
                    JSONArray timeline = data.optJSONArray("timeline");
                    if (timeline != null) for (int i=0;i<timeline.length();i++) {
                        JSONObject slide = timeline.optJSONObject(i);
                        if (slide != null && "slide".equals(slide.optString("type"))) presentations.add(slide.optString("pres"));
                    }
                    presentations.add(data.optString("presentation"));
                    for (String p : presentations) cache(p);
                    JSONArray unlocked = data.optJSONArray("unlockedproblem");
                    if (unlocked != null) for (int i=0;i<unlocked.length();i++) {
                        ws.send(new JSONObject().put("op", "probleminfo").put("lessonid", id)
                                .put("problemid", unlocked.getString(i)).put("msgid", 1).toString());
                    }
                } else if ("unlockproblem".equals(op)) {
                    JSONObject p = data.optJSONObject("problem");
                    if (p != null) {
                        long limit = p.optLong("limit", -2);
                        long deadline = limit == -1 ? -1 : received + limit * 1000L;
                        if (limit == -1 || limit > 0) startProblem(p.optString("sid", p.optString("prob")), deadline, p);
                        else log(name + "：题目缺少有效时限，请在雨课堂查看");
                    }
                } else if ("probleminfo".equals(op)) {
                    long limit = data.optLong("limit", -2);
                    if (limit == -1) startProblem(data.optString("problemid"), -1, data);
                    else if (limit > 0 && data.has("now") && data.has("dt")) {
                        long remainMs = limit*1000L - (data.getLong("now") - data.getLong("dt"));
                        if (remainMs > 0) startProblem(data.optString("problemid"), received + remainMs, data);
                        else log(name + "：历史题目已过期，已忽略");
                    } else log(name + "：题目缺少有效时限，请在雨课堂查看");
                } else if ("presentationupdated".equals(op) || "presentationcreated".equals(op)) cache(data.optString("presentation"));
                else if ("lessonfinished".equals(op)) {
                    closed = true; if (socket != null) socket.close(1000, "课程结束");
                    for (String reviewId : reviewIds) ReviewRegistry.cancel(reviewId);
                    lessons.remove(id, this); log(name + "：课程已结束"); courses(); serial.shutdown();
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
        void startProblem(String problemId, long questionDeadline, JSONObject pushed) {
            if (!running || closed || problemId == null || problemId.isEmpty() || dispatched.contains(problemId)) return;
            JSONObject cached = problems.get(problemId);
            if (cached != null && cached.has("result") && !cached.isNull("result")) { dispatched.add(problemId); return; }
            if ((questionDeadline == -1 || questionDeadline > SystemClock.elapsedRealtime()) && announcedProblems.add(problemId))
                speak(name + "，收到新题目");
            if (!prefs.getBoolean("auto_answer", true)) { log(name + "：检测到题目，自动答题已关闭"); return; }
            if (questionDeadline != -1 && questionDeadline - SystemClock.elapsedRealtime() <= 5000) {
                log(name + "：题目已过期或剩余时间不足，请在雨课堂查看"); return;
            }
            JSONObject problem = mergeProblem(cached, pushed);
            int type = problem.optInt("problemType", -1);
            if (type != 1 && type != 2 && type != 4 && type != 5) { log(name + "：未知或暂不支持的题型，请在雨课堂查看"); return; }
            if (!hasContent(problem)) { manual("未获取到题面，请在雨课堂查看"); return; }
            JSONArray options = problem.optJSONArray("options");
            if ((type == 1 || type == 2) && (options == null || options.length() == 0)) {
                manual("题目缺少选项，请在雨课堂查看"); return;
            }
            if (!dispatched.add(problemId)) return;
            try { workers.execute(() -> answer(problemId, type, problem, questionDeadline)); }
            catch (RejectedExecutionException ignored) { }
        }
        void answer(String problemId, int type, JSONObject problem, long questionDeadline) {
            log(name + "：检测到题目，正在请求大模型");
            try {
                long llmDeadline = questionDeadline == -1 ? SystemClock.elapsedRealtime() + 90000 : questionDeadline - 5000;
                JSONArray answers = askModel(problem, type, llmDeadline);
                if (!running || closed) return;
                long now = SystemClock.elapsedRealtime();
                long remaining = questionDeadline == -1 ? Long.MAX_VALUE : questionDeadline - now - 5000;
                long configured = Math.max(1, Math.min(300, prefs.getInt("review_seconds", 15))) * 1000L;
                long reviewMs = Math.min(configured, remaining);
                if (reviewMs <= 0) { log(name + "：剩余时间不足，请在雨课堂查看"); return; }
                ReviewRegistry.Review review = ReviewRegistry.create(name, problemId, type, problem, answers,
                        now + reviewMs, questionDeadline, (r, cancelled) -> {
                    reviewIds.remove(r.id);
                    Integer n = reviewNotifications.remove(r.id);
                    if (n != null) ((NotificationManager)getSystemService(NOTIFICATION_SERVICE)).cancel(n);
                    changed();
                    if (cancelled || !running || closed) { if (running) log(name + "：本题已取消"); return; }
                    try { workers.execute(() -> submitAnswer(r)); } catch (RejectedExecutionException ignored) { }
                });
                reviewIds.add(review.id);
                if (!running || closed) { ReviewRegistry.cancel(review.id); return; }
                notifyReview(review);
                if (ReviewRegistry.get(review.id) == null) {
                    Integer n = reviewNotifications.remove(review.id);
                    if (n != null) ((NotificationManager)getSystemService(NOTIFICATION_SERVICE)).cancel(n);
                    return;
                }
                try { timers.schedule(() -> ReviewRegistry.submit(review.id),
                        Math.max(0, review.deadlineElapsed - SystemClock.elapsedRealtime()), TimeUnit.MILLISECONDS); }
                catch (RejectedExecutionException e) { ReviewRegistry.cancel(review.id); return; }
                changed(); log(name + "：答案已生成，请在倒计时内核对");
            } catch (Exception e) {
                if (running && !closed) manual("大模型未能给出答案（" + safeMessage(e) + "），请在雨课堂查看");
            }
        }
        void manual(String message) { log(name + "：" + message); notifyCall(name + " · 请手动回答", message); }
        void submitAnswer(ReviewRegistry.Review review) {
            if (!running || closed) return;
            long remaining = review.questionDeadlineElapsed == -1 ? 10000 : review.questionDeadlineElapsed - SystemClock.elapsedRealtime();
            if (remaining <= 0) { log(name + "：题目已超时，未提交"); speak(name + "，题目已超时"); return; }
            try {
                JSONObject payload = new JSONObject().put("problemId", review.problemId).put("problemType", review.type)
                        .put("dt", System.currentTimeMillis()/1000L).put("result", review.answers);
                JSONObject result = callJson(rainRequest("/api/v3/lesson/problem/answer", auth).post(body(payload)).build(), Math.min(10000, remaining));
                if (result.optInt("code", -1) == 0) { log(name + "：答案提交成功"); speak(name + "，答案提交成功"); }
                else { log(name + "：答案提交失败，请在雨课堂查看"); speak(name + "，答案提交失败"); }
            } catch (Exception e) {
                if (running) { log(name + "：提交结果未确认，请在雨课堂核对；不会重复提交"); speak(name + "，提交结果未确认"); }
            }
        }
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
    private JSONArray askModel(JSONObject problem, int type, long deadline) throws Exception {
        String base = prefs.getString("base_url", "").trim().replaceAll("/+$", "");
        if (base.endsWith("/chat/completions")) base = base.substring(0, base.length() - "/chat/completions".length());
        String key=prefs.getString("api_key", "").trim(), model=prefs.getString("model", "").trim();
        if (base.isEmpty() || key.isEmpty() || model.isEmpty()) throw new VisibleFailure("请先配置模型地址、模型名和 API Key");
        URL endpoint;
        try { endpoint = new URL(base + "/chat/completions"); } catch (Exception e) { throw new VisibleFailure("模型地址无效"); }
        if (!"https".equalsIgnoreCase(endpoint.getProtocol()) || endpoint.getUserInfo() != null) throw new VisibleFailure("模型接口必须使用 HTTPS");
        String system = "你是一名答题助手，根据题目和选项作答，只输出 JSON。单选或多选：{\"choices\":[\"A\"]}；填空：{\"blanks\":[\"答案\"]}；简答：{\"answer\":\"答案\"}。图片属于题目的一部分。";
        JSONArray messages = new JSONArray().put(new JSONObject().put("role", "system").put("content", system))
                .put(new JSONObject().put("role", "user").put("content", questionContent(problem, type, deadline)));
        JSONObject payload = new JSONObject().put("model", model).put("temperature", 0).put("messages", messages);
        Request request = new Request.Builder().url(endpoint).header("Authorization", "Bearer " + key).post(body(payload)).build();
        int configured = Math.max(5, Math.min(600, prefs.getInt("timeout", 30)));
        if ("api.deepseek.com".equalsIgnoreCase(endpoint.getHost())) configured = Math.max(30, configured);
        Exception last = null;
        for (int attempt=0;attempt<3;attempt++) {
            if (!running) throw new VisibleFailure("监听已停止");
            long remaining = deadline - SystemClock.elapsedRealtime();
            if (remaining <= 1000) throw new VisibleFailure("答题时间不足");
            try {
                JSONObject response = callJson(request, Math.min(configured * 1000L, remaining));
                String text = response.getJSONArray("choices").getJSONObject(0).getJSONObject("message").optString("content", "");
                int first = text.indexOf('{'), lastBrace = text.lastIndexOf('}');
                if (first < 0 || lastBrace < first) throw new VisibleFailure("模型返回格式无效");
                JSONObject parsed = new JSONObject(text.substring(first, lastBrace+1));
                JSONArray values;
                if (type == 5) values = new JSONArray().put(parsed.optString("answer", "").trim());
                else values = parsed.optJSONArray(type == 4 ? "blanks" : "choices");
                if (!ReviewRegistry.valid(type, values)) throw new VisibleFailure("模型未返回有效答案");
                JSONArray clean = new JSONArray();
                Set<String> optionKeys = new HashSet<>();
                JSONArray options = problem.optJSONArray("options");
                if (options != null) for (int i=0;i<options.length();i++) {
                    JSONObject opt=options.optJSONObject(i);
                    optionKeys.add((opt == null ? String.valueOf((char)('A'+i)) : opt.optString("key", String.valueOf((char)('A'+i)))).toUpperCase(Locale.ROOT));
                }
                Set<String> used = new HashSet<>();
                for (int i=0;i<values.length();i++) {
                    String value=values.getString(i).trim();
                    if (type == 1 || type == 2) {
                        value=value.toUpperCase(Locale.ROOT);
                        if ((!optionKeys.isEmpty() && !optionKeys.contains(value)) || !used.add(value)) throw new VisibleFailure("模型给出的选项无效");
                    }
                    clean.put(value);
                }
                JSONArray blanks=problem.optJSONArray("blanks");
                if (type == 4 && blanks != null && blanks.length()>0 && clean.length()!=blanks.length()) throw new VisibleFailure("模型返回的填空数量不匹配");
                return clean;
            } catch (VisibleFailure e) { throw e; }
            catch (HttpStatus e) { last=e; if (e.code != 429 && e.code != 500 && e.code != 502 && e.code != 503 && e.code != 504) throw e; }
            catch (IOException e) { last=e; }
            long pause=(attempt+1)*1000L;
            if (attempt == 2 || deadline - SystemClock.elapsedRealtime() <= pause + 1000) break;
            try { Thread.sleep(pause); } catch (InterruptedException e) { Thread.currentThread().interrupt(); throw new VisibleFailure("监听已停止"); }
        }
        throw last == null ? new VisibleFailure("请求失败") : last;
    }
}
