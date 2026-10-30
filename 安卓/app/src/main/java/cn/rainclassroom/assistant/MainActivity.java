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
    @Override protected void onResume(){super.onResume();resumed=true;openingReview=false;courses=MonitorService.getCoursesJson();if(home)showHome();maybeOpenReview();}
    @Override protected void onPause(){resumed=false;super.onPause();}
    private LinearLayout screen(String title,String subtitle){
        LinearLayout root=Ui.screen(this,title,subtitle); ScrollView scroll=new ScrollView(this); scroll.setFillViewport(false); currentScroll=scroll;
        content=Ui.column(this); scroll.addView(content); root.addView(scroll,new LinearLayout.LayoutParams(-1,0,1)); setContentView(root); return root;
    }
    private void showHome(){
        int oldScroll=home&&currentScroll!=null?currentScroll.getScrollY():0;
        home=true; screen("雨课堂助手","课堂动态 · 答案核对 · 模型配置");
        final ScrollView newScroll=currentScroll;newScroll.post(()->newScroll.scrollTo(0,oldScroll));
        LinearLayout summary=Ui.card(this); summary.addView(Ui.text(this,"课堂监听",18,Ui.INK,true)); Ui.gap(this,summary,7);
        boolean logged=!prefs.getString("sessionid","").isEmpty();
        summary.addView(Ui.text(this,logged?(MonitorService.isRunning?"已登录 · 正在监听课堂":"已登录 · 监听未启动"):"尚未登录 · 请先登录雨课堂",13,Ui.MUTED,false));
        Button login=Ui.button(this,logged?"更换登录账号":"登录雨课堂",false);
        login.setOnClickListener(v->startActivity(new Intent(this,LoginActivity.class))); Ui.buttonGap(this,summary,login);
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
            for(int i=0;i<list.length();i++){JSONObject obj=list.optJSONObject(i);if(obj!=null){active.addView(Ui.text(this,obj.optString("courseName","未命名课程"),15,Ui.INK,true)); Ui.gap(this,active,6);}}}
        catch(Exception e){active.addView(Ui.text(this,"课程列表暂不可用",13,Ui.MUTED,false));} content.addView(active);
        LinearLayout review=Ui.card(this);review.addView(Ui.text(this,"待核对答案",18,Ui.INK,true));Ui.gap(this,review,8);
        java.util.List<ReviewRegistry.Review> waiting=ReviewRegistry.list();
        if(waiting.isEmpty()) review.addView(Ui.text(this,"暂无待处理的题目",13,Ui.MUTED,false));
        for(ReviewRegistry.Review item:waiting){Button b=Ui.button(this,item.course+" · 点击核对答案",false);String id=item.id;
            b.setOnClickListener(v->openReview(id));Ui.buttonGap(this,review,b);} content.addView(review);
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
        if(!resumed||openingReview||isFinishing())return;
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
        EditText timeout=Ui.input(this,"30",false);timeout.setInputType(InputType.TYPE_CLASS_NUMBER);Ui.field(this,card,"请求超时（秒）",timeout);
        content.addView(card);
        LinearLayout reviewCard=Ui.card(this);reviewCard.addView(Ui.text(this,"答案核对",18,Ui.INK,true));
        EditText seconds=Ui.input(this,"15",false);seconds.setInputType(InputType.TYPE_CLASS_NUMBER);seconds.setText(String.valueOf(prefs.getInt("review_seconds",15)));
        Ui.field(this,reviewCard,"核对倒计时（秒）",seconds);Ui.gap(this,reviewCard,8);
        reviewCard.addView(Ui.text(this,"答案可修改、立即提交或取消，到时自动提交。题目快到期时会缩短核对时间。",12,Ui.MUTED,false));content.addView(reviewCard);
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
            edit.putBoolean("auto_danmu",autoDanmu.isChecked()).putInt("danmu_limit",count);
            edit.putBoolean("audio_on",audioOn.isChecked());
            for(int i=0;i<NAMES.length;i++){Profile p=drafts.get(i);String prefix="provider_"+i+"_";edit.putString(prefix+"url",p.url).putString(prefix+"key",p.key).putString(prefix+"model",p.model).putInt(prefix+"timeout",p.timeout);}
            if(!edit.commit()){toast("保存失败，请检查设备存储空间");return;}toast("设置已保存");showHome();
        });
        cancel.setOnClickListener(v->showHome());
    }
    private static boolean validUrl(String value){try {java.net.URI u=new java.net.URI(value);return "https".equalsIgnoreCase(u.getScheme())&&u.getHost()!=null&&u.getUserInfo()==null&&u.getQuery()==null&&u.getFragment()==null;}catch(Exception e){return false;}}
    private static int parse(String value,int fallback){try{return Integer.parseInt(value.trim());}catch(Exception e){return fallback;}}
    @Override public void onBackPressed(){if(!home)showHome();else super.onBackPressed();}
}
