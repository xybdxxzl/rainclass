package cn.rainclassroom.assistant;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.Bundle;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.widget.*;
import org.json.JSONArray;
import org.json.JSONObject;
import java.util.HashMap;
import java.util.HashSet;

public class MainActivity extends Activity {
    private SharedPreferences prefs;
    private LinearLayout content;
    private ScrollView currentScroll;
    private final StringBuilder logs=new StringBuilder();
    private String courses="[]";
    private boolean home=true, pendingStart=false;
    private int screenGeneration;
    private String verifyingSession="";
    private boolean resumed=false, openingReview=false;
    private final HashSet<String> seenReviews=new HashSet<>();
    private static final String UPDATE="cn.rainclassroom.assistant.UPDATE";
    private static final String[] NAMES={"DeepSeek","千问 · 北京","千问 · 国际","Gemini","自定义接口"};
    private static final String[] URLS={"https://api.deepseek.com","https://dashscope.aliyuncs.com/compatible-mode/v1","https://dashscope-intl.aliyuncs.com/compatible-mode/v1","https://generativelanguage.googleapis.com/v1beta/openai",""};
    private static final String[] MODELS={"deepseek-flash","qwen-plus","qwen-plus","gemini-3.8-flash",""};
    private final BroadcastReceiver receiver=new BroadcastReceiver(){
        @Override public void onReceive(Context c, Intent i){
            String s=i.getStringExtra("log");
            if(s!=null && !s.trim().isEmpty()){logs.append(s).append('\n'); if(logs.length()>12000) logs.delete(0,logs.length()-12000);}
            s=i.getStringExtra("courses"); if(s!=null) courses=s;
            if(home) showHome();
            maybeOpenReview();
        }
    };
    @Override public void onCreate(Bundle b){super.onCreate(b);prefs=getSharedPreferences("rain_config",MODE_PRIVATE); showHome();}
    @Override protected void onStart(){super.onStart(); IntentFilter f=new IntentFilter(UPDATE);
        if(Build.VERSION.SDK_INT>=33) registerReceiver(receiver,f,Context.RECEIVER_NOT_EXPORTED); else registerReceiver(receiver,f);
    }
    @Override protected void onStop(){unregisterReceiver(receiver); super.onStop();}
    @Override protected void onResume(){super.onResume();resumed=true;openingReview=false;courses=MonitorService.getCoursesJson();if(home)showHome();verifyAccount();maybeOpenReview();}
    @Override protected void onPause(){resumed=false;super.onPause();}
    private LinearLayout screen(String title,String subtitle){
        screenGeneration++;
        LinearLayout root=Ui.screen(this,title,subtitle); ScrollView scroll=new ScrollView(this); scroll.setFillViewport(false); currentScroll=scroll;
        content=Ui.column(this); scroll.addView(content); root.addView(scroll,new LinearLayout.LayoutParams(-1,0,1)); setContentView(root); return root;
    }
    private void showHome(){
        int oldScroll=home&&currentScroll!=null?currentScroll.getScrollY():0;
        home=true; screen("雨课堂助手","课堂动态 · 答案核对 · 模型配置");
        final ScrollView newScroll=currentScroll;newScroll.post(()->newScroll.scrollTo(0,oldScroll));
        LinearLayout summary=Ui.card(this); summary.addView(Ui.text(this,"课堂监听",18,Ui.INK,true)); Ui.gap(this,summary,7);
        boolean logged=!prefs.getString("sessionid","").isEmpty();
        String user=prefs.getString("user_name","");
        summary.addView(Ui.text(this,"当前用户："+(user.isEmpty()?"待确认":user),16,Ui.INK,true));
        summary.addView(Ui.text(this,!logged?"尚未登录":prefs.getBoolean("account_verified",false)?"账号已验证":"账号待验证或已失效",13,Ui.MUTED,false));
        try{JSONObject state=new JSONObject(MonitorService.getStatusJson());
            summary.addView(Ui.text(this,"监听状态："+(MonitorService.isRunning?state.optString("state","正在启动"):"未启动"),14,Ui.BLUE,true));
            summary.addView(Ui.text(this,"最近课程检测："+clock(state.optLong("lastScan"))+"\n最近课堂消息："+clock(state.optLong("lastMessage"))
                +"\n最近收到题目："+clock(state.optLong("lastQuestion"))+" · 已收 "+state.optInt("received")+" 题",12,Ui.MUTED,false));
        }catch(Exception ignored){}
        Button login=Ui.button(this,logged?"更换登录账号":"登录雨课堂",false);
        login.setOnClickListener(v->{stopService(new Intent(this,MonitorService.class));startActivity(new Intent(this,LoginActivity.class));}); Ui.buttonGap(this,summary,login);
        LinearLayout row=new LinearLayout(this); row.setOrientation(LinearLayout.HORIZONTAL);
        Button start=Ui.button(this,"开始监听",true), stop=Ui.button(this,"停止",false);
        LinearLayout.LayoutParams p=new LinearLayout.LayoutParams(0,Ui.dp(this,48),1); p.rightMargin=Ui.dp(this,8);
        row.addView(start,p);row.addView(stop,new LinearLayout.LayoutParams(Ui.dp(this,100),Ui.dp(this,48)));
        Ui.gap(this,summary,10);summary.addView(row);content.addView(summary);
        start.setOnClickListener(v->requestStart());
        stop.setOnClickListener(v->{try {startService(new Intent(this,MonitorService.class).setAction("cn.rainclassroom.assistant.STOP"));
            toast("已请求停止监听并取消待答");}catch(Exception ex){toast("停止失败，请重试");}});
        LinearLayout active=Ui.card(this);active.addView(Ui.text(this,"正在上课",18,Ui.INK,true)); Ui.gap(this,active,8);
        try {JSONArray list=new JSONArray(courses);if(list.length()==0)active.addView(Ui.text(this,"暂无课程 · 开始监听后自动刷新",13,Ui.MUTED,false));
            for(int i=0;i<list.length();i++){JSONObject obj=list.optJSONObject(i);if(obj!=null){active.addView(Ui.text(this,obj.optString("courseName","未命名课程")+" · "+obj.optString("state","连接中"),15,Ui.INK,true)); Ui.gap(this,active,6);}}}
        catch(Exception e){active.addView(Ui.text(this,"课程列表暂不可用",13,Ui.MUTED,false));} content.addView(active);
        LinearLayout review=Ui.card(this);review.addView(Ui.text(this,"实时题目",18,Ui.INK,true));Ui.gap(this,review,8);
        java.util.List<ReviewRegistry.Review> waiting=ReviewRegistry.list();
        if(waiting.isEmpty()) review.addView(Ui.text(this,"本次尚未收到待处理题目，请同时检查上方监听状态。",13,Ui.MUTED,false));
        for(ReviewRegistry.Review item:waiting){Button b=Ui.button(this,item.course+" · 模型"+item.modelState+" · 点击作答",false);String id=item.id;
            b.setOnClickListener(v->openReview(id));Ui.buttonGap(this,review,b);} content.addView(review);
        LinearLayout historyCard=Ui.card(this);historyCard.addView(Ui.text(this,"历史题目",18,Ui.INK,true));
        historyCard.addView(Ui.text(this,"包含模型超时、失败和未作答的题目，保存在本机。",12,Ui.MUTED,false));
        Button history=Ui.button(this,"查看历史题目",false);history.setOnClickListener(v->showHistory());Ui.buttonGap(this,historyCard,history);content.addView(historyCard);
        LinearLayout prefsCard=Ui.card(this);prefsCard.addView(Ui.text(this,"偏好设置",18,Ui.INK,true));
        prefsCard.addView(Ui.text(this,"设置模型、密钥和答案核对时间",13,Ui.MUTED,false));
        Button settings=Ui.button(this,"配置大模型与答题",false); settings.setOnClickListener(v->showSettings());Ui.buttonGap(this,prefsCard,settings);content.addView(prefsCard);
        LinearLayout logCard=Ui.card(this);logCard.addView(Ui.text(this,"运行记录",18,Ui.INK,true));Ui.gap(this,logCard,8);
        logCard.addView(Ui.text(this,logs.length()==0?"监听状态将在这里显示":logs.toString(),12,Ui.MUTED,false));content.addView(logCard);
    }
    private void requestStart(){
        if(prefs.getString("sessionid","").isEmpty()){toast("请先登录雨课堂");return;}
        if(Build.VERSION.SDK_INT>=33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)!=PackageManager.PERMISSION_GRANTED){
            pendingStart=true;new AlertDialog.Builder(this).setTitle("开启课堂提醒").setMessage("通知用于在手机后台提醒你查看题目；关闭通知可能无法及时看到答案。")
                .setPositiveButton("继续",(d,w)->requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS},100))
                .setNegativeButton("取消",(d,w)->pendingStart=false).show();return;
        }
        startMonitor();
    }
    @Override public void onRequestPermissionsResult(int req,String[] permissions,int[] grants){super.onRequestPermissionsResult(req,permissions,grants);
        if(req==100 && pendingStart){pendingStart=false;if(grants.length==0 || grants[0]!=PackageManager.PERMISSION_GRANTED) toast("通知未开启，后台题目提醒可能不可见");startMonitor();}}
    private void startMonitor(){try {Intent intent=new Intent(this,MonitorService.class); if(Build.VERSION.SDK_INT>=26)startForegroundService(intent);else startService(intent);toast("正在启动监听");}
        catch(Exception e){toast("无法启动监听，请检查系统设置");}}
    private void openReview(String id){seenReviews.add(id);openingReview=true;startActivity(new Intent(this,ReviewActivity.class).putExtra("review_id",id));}
    private void maybeOpenReview(){
        if(!home||!resumed||openingReview||isFinishing())return;
        for(ReviewRegistry.Review r:ReviewRegistry.list())if(!seenReviews.contains(r.id)){openReview(r.id);return;}
    }
    private void toast(String s){Toast.makeText(this,s,Toast.LENGTH_LONG).show();}
    private static final class Profile {String url,key,model;int timeout; Profile(String u,String k,String m,int t){url=u;key=k;model=m;timeout=t;}}
    private void showSettings(){
        home=false;LinearLayout settingsRoot=screen("偏好设置","选择服务商，核对答案后提交");
        LinearLayout card=Ui.card(this);card.addView(Ui.text(this,"大模型连接",18,Ui.INK,true));
        Switch enabled=new Switch(this);enabled.setText("启用大模型与自动答题");enabled.setTextColor(Ui.INK);enabled.setChecked(prefs.getBoolean("auto_answer",true));Ui.gap(this,card,12);card.addView(enabled);
        Spinner provider=new Spinner(this); provider.setAdapter(new ArrayAdapter<>(this,android.R.layout.simple_spinner_dropdown_item,NAMES));Ui.field(this,card,"服务商",provider);
        EditText key=Ui.input(this,"粘贴当前服务商的 API Key",true);Ui.field(this,card,"API Key",key);
        CheckBox visible=new CheckBox(this);visible.setText("显示密钥");visible.setTextColor(Ui.MUTED);card.addView(visible);
        visible.setOnCheckedChangeListener((b,show)->{key.setInputType(InputType.TYPE_CLASS_TEXT | (show?InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD:InputType.TYPE_TEXT_VARIATION_PASSWORD));key.setSelection(key.length());});
        EditText model=Ui.input(this,"例如 deepseek-flash",false);Ui.field(this,card,"模型名称",model);
        EditText url=Ui.input(this,"https://...",false);Ui.field(this,card,"接口地址",url);
        card.addView(Ui.text(this,"支持完整的 /chat/completions 地址；密钥按服务商分别保存。",12,Ui.MUTED,false));
        EditText timeout=Ui.input(this,"30",false);timeout.setInputType(InputType.TYPE_CLASS_NUMBER);Ui.field(this,card,"总等待上限（秒，含图片和重试）",timeout);
        TextView testStatus=Ui.text(this,"未测试当前配置",12,Ui.MUTED,false);
        Button test=Ui.button(this,"测试大模型连接",false);Ui.buttonGap(this,card,test);card.addView(testStatus);
        final int mounted=screenGeneration;final int[] configVersion={0};
        android.text.TextWatcher watcher=new android.text.TextWatcher(){
            public void beforeTextChanged(CharSequence t,int st,int count,int after){}
            public void onTextChanged(CharSequence t,int st,int before,int count){configVersion[0]++;testStatus.setText("配置已变更，请重新测试");}
            public void afterTextChanged(android.text.Editable e){}
        };
        key.addTextChangedListener(watcher);model.addTextChangedListener(watcher);url.addTextChangedListener(watcher);timeout.addTextChangedListener(watcher);
        test.setOnClickListener(v->{
            String testUrl=url.getText().toString().trim(),testKey=key.getText().toString().trim(),testModel=model.getText().toString().trim();
            int t=parse(timeout.getText().toString(),-1);
            if(!validUrl(ModelClient.normalize(testUrl))||testKey.isEmpty()||testModel.isEmpty()||t<5||t>600){testStatus.setText("请先填写有效地址、密钥、模型和等待上限");return;}
            int version=configVersion[0];test.setEnabled(false);testStatus.setText("测试中…使用当前填写的配置，最多等待 "+Math.min(30,t)+" 秒");
            new Thread(()->{
                long begin=android.os.SystemClock.elapsedRealtime();String result;
                try{ModelClient.test(testUrl,testKey,testModel,t);result="测试通过 · "+testModel;}
                catch(Exception e){result=e instanceof ModelClient.Failure?e.getMessage():e instanceof java.io.InterruptedIOException?"模型连接超时":"网络或响应异常";}
                final String message=result+" · "+(android.os.SystemClock.elapsedRealtime()-begin)+" ms · "+clock(System.currentTimeMillis());
                runOnUiThread(()->{if(isFinishing()||screenGeneration!=mounted)return;test.setEnabled(true);testStatus.setText(configVersion[0]==version?message:"配置已变更，刚才的测试结果已失效");});
            },"模型连接测试").start();
        });
        content.addView(card);
        LinearLayout reviewCard=Ui.card(this);reviewCard.addView(Ui.text(this,"答案核对",18,Ui.INK,true));
        EditText seconds=Ui.input(this,"15",false);seconds.setInputType(InputType.TYPE_CLASS_NUMBER);seconds.setText(String.valueOf(prefs.getInt("review_seconds",15)));
        Ui.field(this,reviewCard,"核对倒计时（秒）",seconds);Ui.gap(this,reviewCard,8);
        Switch autoSubmit=new Switch(this);autoSubmit.setText("模型成功后允许自动提交");autoSubmit.setTextColor(Ui.INK);autoSubmit.setChecked(prefs.getBoolean("auto_submit",true));reviewCard.addView(autoSubmit);
        reviewCard.addView(Ui.text(this,"收到题目即可手动作答。人工修改后本题暂停自动提交；题目快到期时会缩短核对时间。",12,Ui.MUTED,false));content.addView(reviewCard);
        LinearLayout danmuCard=Ui.card(this);danmuCard.addView(Ui.text(this,"课堂弹幕",18,Ui.INK,true));Ui.gap(this,danmuCard,8);
        Switch autoDanmu=new Switch(this);autoDanmu.setText("启用自动跟随弹幕");autoDanmu.setTextColor(Ui.INK);autoDanmu.setChecked(prefs.getBoolean("auto_danmu",false));danmuCard.addView(autoDanmu);
        EditText danmuLimit=Ui.input(this,"5",false);danmuLimit.setInputType(InputType.TYPE_CLASS_NUMBER);danmuLimit.setText(String.valueOf(prefs.getInt("danmu_limit",5)));
        Ui.field(this,danmuCard,"相同弹幕触发阈值（条）",danmuLimit);content.addView(danmuCard);
        LinearLayout audioCard=Ui.card(this);audioCard.addView(Ui.text(this,"语音提醒",18,Ui.INK,true));Ui.gap(this,audioCard,8);
        Switch audioOn=new Switch(this);audioOn.setText("语音播报题目、提交结果和本人点名");audioOn.setTextColor(Ui.INK);
        audioOn.setChecked(prefs.getBoolean("audio_on",false));audioCard.addView(audioOn);
        audioCard.addView(Ui.text(this,"使用手机的文字转语音服务；默认关闭。",12,Ui.MUTED,false));content.addView(audioCard);
        HashMap<Integer,Profile> drafts=new HashMap<>();
        int initial=prefs.getInt("provider_index",-1);String currentUrl=prefs.getString("base_url",URLS[0]);
        if(initial<0 || initial>=NAMES.length){initial=4;for(int i=0;i<4;i++)if(URLS[i].equals(currentUrl)) initial=i;}
        for(int i=0;i<NAMES.length;i++){
            String prefix="provider_"+i+"_";
            drafts.put(i,new Profile(prefs.getString(prefix+"url",URLS[i]),prefs.getString(prefix+"key",""),prefs.getString(prefix+"model",MODELS[i]),prefs.getInt(prefix+"timeout",30)));
        }
        drafts.put(initial,new Profile(currentUrl,prefs.getString("api_key",""),prefs.getString("model",MODELS[initial]),prefs.getInt("timeout",30)));
        final int[] current={initial};
        Profile first=drafts.get(initial);key.setText(first.key);model.setText(first.model);url.setText(first.url);timeout.setText(String.valueOf(first.timeout));provider.setSelection(initial);
        provider.setOnItemSelectedListener(new android.widget.AdapterView.OnItemSelectedListener(){
            public void onNothingSelected(android.widget.AdapterView<?> v){}
            public void onItemSelected(android.widget.AdapterView<?> v,View item,int position,long id){
                if(position==current[0])return;
                int t=parse(timeout.getText().toString(),30);drafts.put(current[0],new Profile(url.getText().toString(),key.getText().toString(),model.getText().toString(),t));
                current[0]=position;Profile p=drafts.get(position);visible.setChecked(false);url.setText(p.url);key.setText(p.key);model.setText(p.model);timeout.setText(String.valueOf(p.timeout));
            }
        });
        LinearLayout actions=new LinearLayout(this);actions.setOrientation(LinearLayout.HORIZONTAL);
        Button cancel=Ui.button(this,"取消",false), save=Ui.button(this,"保存设置",true);
        LinearLayout.LayoutParams actionLeft=new LinearLayout.LayoutParams(0,Ui.dp(this,48),1);actionLeft.rightMargin=Ui.dp(this,8);
        actions.addView(cancel,actionLeft);actions.addView(save,new LinearLayout.LayoutParams(0,Ui.dp(this,48),1));
        Ui.gap(this,settingsRoot,8);settingsRoot.addView(actions);
        save.setOnClickListener(v->{
            int t=parse(timeout.getText().toString(),-1), sec=parse(seconds.getText().toString(),-1), count=parse(danmuLimit.getText().toString(),-1);
            String address=url.getText().toString().trim();while(address.endsWith("/"))address=address.substring(0,address.length()-1);
            if(address.endsWith("/chat/completions"))address=address.substring(0,address.length()-17);
            String token=key.getText().toString().trim(),name=model.getText().toString().trim();
            if(t<5||t>600||sec<1||sec>300){toast("超时请输入 5–600 秒，核对时间请输入 1–300 秒");return;}
            if(count<1||count>100){toast("弹幕阈值请输入 1–100 条");return;}
            if(enabled.isChecked() && (!validUrl(address)||token.isEmpty()||token.matches(".*\\s+.*")||name.isEmpty()||name.matches(".*\\s+.*"))){toast("请检查接口地址、API Key 和模型名称");return;}
            drafts.put(current[0],new Profile(address,token,name,t));
            SharedPreferences.Editor edit=prefs.edit().putBoolean("auto_answer",enabled.isChecked()).putInt("provider_index",current[0])
                .putString("base_url",address).putString("api_key",token).putString("model",name).putInt("timeout",t).putInt("review_seconds",sec);
            edit.putBoolean("auto_submit",autoSubmit.isChecked());
            if(!autoSubmit.isChecked())for(ReviewRegistry.Review r:ReviewRegistry.list())ReviewRegistry.touch(r.id);
            edit.putBoolean("auto_danmu",autoDanmu.isChecked()).putInt("danmu_limit",count);
            edit.putBoolean("audio_on",audioOn.isChecked());
            for(int i=0;i<NAMES.length;i++){Profile p=drafts.get(i);String prefix="provider_"+i+"_";edit.putString(prefix+"url",p.url).putString(prefix+"key",p.key).putString(prefix+"model",p.model).putInt(prefix+"timeout",p.timeout);}
            if(!edit.commit()){toast("保存失败，请检查设备存储空间");return;}toast("设置已保存");showHome();
        });
        cancel.setOnClickListener(v->showHome());
    }
    private static String clock(long value){return value<=0?"尚无记录":new java.text.SimpleDateFormat("HH:mm:ss",java.util.Locale.ROOT).format(new java.util.Date(value));}
    private void verifyAccount(){
        String token=prefs.getString("sessionid","");if(token.isEmpty()||token.equals(verifyingSession))return;
        if(prefs.getBoolean("account_verified",false)&&System.currentTimeMillis()-prefs.getLong("account_verified_at",0)<60000)return;
        verifyingSession=token;
        new Thread(()->{
            okhttp3.OkHttpClient client=new okhttp3.OkHttpClient.Builder().callTimeout(15,java.util.concurrent.TimeUnit.SECONDS).followSslRedirects(false).build();
            boolean valid=false;JSONObject user=null;
            try(okhttp3.Response response=client.newCall(new okhttp3.Request.Builder().url("https://www.yuketang.cn/api/v3/user/basic-info").header("Cookie","sessionid="+token).build()).execute()){
                if(response.isSuccessful()&&response.body()!=null){JSONObject result=new JSONObject(response.body().string());valid=result.optInt("code",-1)==0;user=result.optJSONObject("data");}
            }catch(Exception ignored){}
            final boolean ok=valid;final JSONObject info=user;
            runOnUiThread(()->{verifyingSession="";if(!token.equals(prefs.getString("sessionid",""))||isFinishing())return;
                SharedPreferences.Editor e=prefs.edit().putBoolean("account_verified",ok);
                if(ok&&info!=null)e.putString("user_id",info.optString("id","")).putString("user_name",info.optString("name","")).putLong("account_verified_at",System.currentTimeMillis());
                e.apply();if(home)showHome();
            });
            client.connectionPool().evictAll();client.dispatcher().executorService().shutdown();
        },"账号验证").start();
    }
    private void showHistory(){
        home=false;screen("历史题目","按当前账号保存在本机，已结束题目仅供查看");
        String account=prefs.getString("user_id","");
        if(account.isEmpty()){content.addView(Ui.text(this,"请先验证登录账号后查看对应历史。",14,Ui.MUTED,false));return;}
        LinearLayout filters=Ui.card(this);
        EditText keyword=Ui.input(this,"搜索题干或答案",false),course=Ui.input(this,"课程名称（可留空）",false),date=Ui.input(this,"yyyy-MM-dd（可留空）",false);
        Ui.field(this,filters,"关键词",keyword);Ui.field(this,filters,"课程",course);Ui.field(this,filters,"日期",date);
        Spinner state=new Spinner(this);String[] labels={"全部","待作答","提交成功","提交失败","提交结果未知","已取消","已过期","已作答"};
        state.setAdapter(new ArrayAdapter<>(this,android.R.layout.simple_spinner_dropdown_item,labels));Ui.field(this,filters,"作答状态",state);
        Spinner modelState=new Spinner(this);String[] modelLabels={"全部","未请求","处理中","重试中","成功","超时","失败","题面获取失败"};
        modelState.setAdapter(new ArrayAdapter<>(this,android.R.layout.simple_spinner_dropdown_item,modelLabels));Ui.field(this,filters,"模型状态",modelState);
        Button search=Ui.button(this,"查询",true);Ui.buttonGap(this,filters,search);content.addView(filters);
        LinearLayout rows=Ui.column(this);content.addView(rows);
        Runnable query=()->{
            rows.removeAllViews();String d=date.getText().toString().trim();
            if(!d.isEmpty()&&!d.matches("\\d{4}-\\d{2}-\\d{2}")){rows.addView(Ui.text(this,"日期格式请输入 yyyy-MM-dd",14,Ui.MUTED,false));return;}
            try(HistoryStore store=new HistoryStore(this)){
                JSONArray list=store.list(account,keyword.getText().toString(),course.getText().toString(),String.valueOf(state.getSelectedItem()),d);
                int count=0;
                for(int i=0;i<list.length();i++){JSONObject item=list.optJSONObject(i);if(item==null)continue;
                    String m=String.valueOf(modelState.getSelectedItem());if(!"全部".equals(m)&&!m.equals(item.optString("modelState")))continue;count++;
                    JSONObject p=item.optJSONObject("problem");String text=p==null?"题面未获取":firstText(p);
                    Button b=Ui.button(this,item.optString("course")+" · "+item.optString("state")+" · 模型"+item.optString("modelState")+"\n"
                        +new java.text.SimpleDateFormat("MM-dd HH:mm",java.util.Locale.ROOT).format(new java.util.Date(item.optLong("received")))+"\n"+(text.length()>65?text.substring(0,65)+"…":text),false);
                    b.setSingleLine(false);b.setMaxLines(5);Ui.buttonGap(this,rows,b);b.setOnClickListener(v->historyDetail(item));
                }
                if(count==0)rows.addView(Ui.text(this,"没有符合条件的历史题目。",14,Ui.MUTED,false));
            }catch(Exception e){rows.addView(Ui.text(this,"历史题目读取失败，请检查存储空间。",14,Ui.MUTED,false));}
        };
        search.setOnClickListener(v->query.run());query.run();
    }
    private static String firstText(JSONObject p){
        String text=p.optString("body","");if(text.isEmpty())text=p.optString("content","");if(text.isEmpty())text=p.optString("title","");
        return android.text.Html.fromHtml(text,android.text.Html.FROM_HTML_MODE_COMPACT).toString().trim();
    }
    private void historyDetail(JSONObject item){
        JSONObject p=item.optJSONObject("problem");StringBuilder text=new StringBuilder(item.optString("course")).append("\n接收时间：")
            .append(new java.text.SimpleDateFormat("yyyy-MM-dd HH:mm:ss",java.util.Locale.ROOT).format(new java.util.Date(item.optLong("received"))))
            .append("\n题目编号：").append(item.optString("problemId")).append("\n\n").append(p==null?"未获取到题面":firstText(p));
        JSONArray options=p==null?null:p.optJSONArray("options");if(options!=null)for(int i=0;i<options.length();i++){
            JSONObject o=options.optJSONObject(i);text.append("\n").append(o==null?String.valueOf((char)('A'+i)):o.optString("key",String.valueOf((char)('A'+i)))).append(". ")
                .append(android.text.Html.fromHtml(o==null?String.valueOf(options.opt(i)):o.optString("value",o.optString("content","")),android.text.Html.FROM_HTML_MODE_COMPACT));
        }
        text.append("\n\n模型：").append(item.optString("modelState")).append(" · ").append(item.optLong("modelDuration")).append(" ms\n")
            .append(item.optString("modelMessage")).append("\n模型建议：").append(item.optJSONArray("suggestions"))
            .append("\n当前答案：").append(item.optJSONArray("answers")).append("\n提交结果：").append(item.optString("state"));
        TextView view=Ui.text(this,text.toString(),15,Ui.INK,false);view.setTextIsSelectable(true);view.setPadding(Ui.dp(this,20),Ui.dp(this,12),Ui.dp(this,20),Ui.dp(this,12));ScrollView scroll=new ScrollView(this);scroll.addView(view);
        new AlertDialog.Builder(this).setTitle("题目详情（只读）").setView(scroll).setPositiveButton("关闭",null).show();
    }
    private static boolean validUrl(String value){try {java.net.URI u=new java.net.URI(value);return "https".equalsIgnoreCase(u.getScheme())&&u.getHost()!=null&&u.getUserInfo()==null&&u.getQuery()==null&&u.getFragment()==null;}catch(Exception e){return false;}}
    private static int parse(String value,int fallback){try{return Integer.parseInt(value.trim());}catch(Exception e){return fallback;}}
    @Override public void onBackPressed(){if(!home)showHome();else super.onBackPressed();}
}
