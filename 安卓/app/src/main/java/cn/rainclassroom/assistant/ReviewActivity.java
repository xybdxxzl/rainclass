package cn.rainclassroom.assistant;

import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.text.Editable;
import android.text.Html;
import android.text.TextWatcher;
import android.view.View;
import android.widget.*;
import org.json.JSONArray;
import org.json.JSONObject;
import java.util.ArrayList;
import java.util.List;

public class ReviewActivity extends Activity {
    private String id;
    private ReviewRegistry.Review review;
    private TextView countdown,validation;
    private Button submit;
    private final Handler handler=new Handler(Looper.getMainLooper());
    private final List<EditText> inputs=new ArrayList<>();
    private final List<CompoundButton> options=new ArrayList<>();
    private final List<String> keys=new ArrayList<>();
    private boolean initializing;
    private final Runnable ticker=new Runnable(){@Override public void run(){
        ReviewRegistry.Review active=ReviewRegistry.get(id);
        if(active==null){if(!isFinishing())finish();return;}
        long left=Math.max(0,active.deadlineElapsed-SystemClock.elapsedRealtime());
        countdown.setText(left>0?((left+999)/1000)+" 秒后自动提交":"正在处理自动提交…");
        if(left<=0)submit.setEnabled(false);
        handler.postDelayed(this,250);
    }};
    @Override protected void onCreate(Bundle b){super.onCreate(b);load(getIntent());}
    @Override protected void onNewIntent(Intent intent){super.onNewIntent(intent);setIntent(intent);load(intent);}
    private void load(Intent intent){
        handler.removeCallbacks(ticker);id=intent.getStringExtra("review_id");review=id==null?null:ReviewRegistry.get(id);
        if(review==null){Toast.makeText(this,"该题已提交、取消或过期",Toast.LENGTH_LONG).show();finish();return;}
        inputs.clear();options.clear();keys.clear();initializing=true;
        LinearLayout root=Ui.screen(this,"答案核对",review.course+" · 可修改后提交");
        ScrollView scroll=new ScrollView(this);LinearLayout content=Ui.column(this);scroll.addView(content);root.addView(scroll,new LinearLayout.LayoutParams(-1,0,1));
        LinearLayout info=Ui.card(this);countdown=Ui.text(this,"",18,Ui.BLUE,true);info.addView(countdown);Ui.gap(this,info,10);
        JSONObject p=review.problem;
        String body=p.optString("body","");
        if(body.isEmpty())body=p.optString("content","");
        if(body.isEmpty())body=p.optString("title","");
        info.addView(Ui.text(this,plain(body),16,Ui.INK,false));
        JSONArray opts=p.optJSONArray("options");if(opts==null)opts=new JSONArray();
        boolean images=body.toLowerCase(java.util.Locale.ROOT).contains("<img")||p.has("images");
        for(int i=0;i<opts.length();i++){
            Object o=opts.opt(i);JSONObject opt=o instanceof JSONObject?(JSONObject)o:null;
            String key=opt!=null?opt.optString("key",String.valueOf((char)('A'+i))):String.valueOf((char)('A'+i));
            String text=opt!=null?opt.optString("value",opt.optString("content","")):String.valueOf(o);
            images|=text.toLowerCase(java.util.Locale.ROOT).contains("<img")||(opt!=null&&opt.has("images"));
            Ui.gap(this,info,4);info.addView(Ui.text(this,key+". "+plain(text),14,Ui.INK,false));
        }
        if(images){Ui.gap(this,info,8);info.addView(Ui.text(this,"本题包含图片，请同时在雨课堂查看原图核对。",12,Ui.MUTED,false));}
        content.addView(info);
        LinearLayout answer=Ui.card(this);answer.addView(Ui.text(this,"修改答案",18,Ui.INK,true));Ui.gap(this,answer,8);
        JSONArray saved=review.answers;
        if(review.type==1||review.type==2){
            RadioGroup radios=review.type==1?new RadioGroup(this):null;
            if(radios!=null){radios.setOrientation(RadioGroup.VERTICAL);answer.addView(radios);}
            int n=opts.length()==0?4:opts.length();
            for(int i=0;i<n;i++){
                JSONObject opt=opts.optJSONObject(i);String key=opt!=null?opt.optString("key",String.valueOf((char)('A'+i))):String.valueOf((char)('A'+i));
                CompoundButton choice=review.type==1?new RadioButton(this):new CheckBox(this);choice.setText(key);choice.setTextColor(Ui.INK);
                choice.setId(View.generateViewId());choice.setChecked(contains(saved,key));options.add(choice);keys.add(key.toUpperCase(java.util.Locale.ROOT));
                if(radios!=null)radios.addView(choice);else answer.addView(choice);
                choice.setOnCheckedChangeListener((b,checked)->changed());
            }
        }else{
            int count=Math.max(1,saved.length());
            for(int i=0;i<count;i++){
                EditText input=Ui.input(this,review.type==4?"填写答案":"编辑参考答案",false);
                if(review.type==5){input.setSingleLine(false);input.setMinLines(3);input.setGravity(android.view.Gravity.TOP);}
                input.setText(saved.optString(i,""));inputs.add(input);Ui.field(this,answer,review.type==4?"第 "+(i+1)+" 空":"答案",input);
                input.addTextChangedListener(new TextWatcher(){public void beforeTextChanged(CharSequence s,int start,int count,int after){}public void onTextChanged(CharSequence s,int start,int before,int count){changed();}public void afterTextChanged(Editable e){}});
            }
        }
        validation=Ui.text(this,"到时提交最近一次有效答案。返回后仍继续计时。",12,Ui.MUTED,false);Ui.gap(this,answer,10);answer.addView(validation);content.addView(answer);
        setContentView(root);
        LinearLayout actions=new LinearLayout(this);Button cancel=Ui.button(this,"取消本题",false);submit=Ui.button(this,"立即提交",true);
        LinearLayout.LayoutParams left=new LinearLayout.LayoutParams(0,Ui.dp(this,48),1);left.rightMargin=Ui.dp(this,8);actions.addView(cancel,left);actions.addView(submit,new LinearLayout.LayoutParams(0,Ui.dp(this,48),1));Ui.gap(this,root,8);root.addView(actions);
        cancel.setOnClickListener(v->{if(ReviewRegistry.cancel(id))Toast.makeText(this,"已取消本题",Toast.LENGTH_SHORT).show();finish();});
        submit.setOnClickListener(v->{JSONArray values=collect();if(values==null){changed();return;}
            if(ReviewRegistry.update(id,values)&&ReviewRegistry.submit(id)){Toast.makeText(this,"正在提交答案",Toast.LENGTH_SHORT).show();finish();}
            else{Toast.makeText(this,"该题已处理或已过期",Toast.LENGTH_LONG).show();finish();}});
        initializing=false;changed();handler.post(ticker);
    }
    private static String plain(String value){return Html.fromHtml(value==null?"":value,Html.FROM_HTML_MODE_COMPACT).toString().trim();}
    private static boolean contains(JSONArray values,String value){for(int i=0;i<values.length();i++)if(value.equalsIgnoreCase(values.optString(i)))return true;return false;}
    private JSONArray collect(){JSONArray result=new JSONArray();
        if(review.type==1||review.type==2){for(int i=0;i<options.size();i++)if(options.get(i).isChecked())result.put(keys.get(i));}
        else for(EditText e:inputs){String value=e.getText().toString().trim();if(value.isEmpty())return null;result.put(value);}
        return result.length()==0?null:result;
    }
    private void changed(){if(initializing||submit==null)return;JSONArray values=collect();boolean valid=values!=null;
        submit.setEnabled(valid);validation.setText(valid?"到时提交当前答案。返回后仍继续计时。":"答案不能为空，到时会提交最近一次有效答案；也可取消本题。");
        if(valid)ReviewRegistry.update(id,values);
    }
    @Override protected void onDestroy(){handler.removeCallbacks(ticker);super.onDestroy();}
}
