package cn.rainclassroom.assistant;

import android.app.Activity;
import android.content.Intent;
import android.os.*;
import android.text.*;
import android.view.*;
import android.widget.*;
import org.json.*;
import java.util.*;

/** 题面一到即可编辑；定时刷新只更新状态，不重建正在输入的控件。 */
public class ReviewActivity extends Activity {
    private String id,problemSignature="";
    private ReviewRegistry.Review review;
    private TextView countdown,validation,modelStatus,suggestions;
    private Button submit,adopt,retry;
    private final Handler handler=new Handler(Looper.getMainLooper());
    private final List<EditText> inputs=new ArrayList<>();
    private final List<CompoundButton> options=new ArrayList<>();
    private final List<String> keys=new ArrayList<>();
    private boolean initializing;
    private String shownAnswers="";
    private final Runnable ticker=new Runnable(){@Override public void run(){
        ReviewRegistry.Review active=ReviewRegistry.get(id);
        if(active==null){if(!isFinishing()){Toast.makeText(ReviewActivity.this,"本题已结束，可在历史题目查看结果",Toast.LENGTH_SHORT).show();finish();}return;}
        if(!active.touched&&!problemSignature.equals(active.problem.toString())){load(getIntent());return;}
        long now=SystemClock.elapsedRealtime();
        long left=active.questionDeadlineElapsed==-2?-2:active.questionDeadlineElapsed==-1?-1:Math.max(0,active.questionDeadlineElapsed-now);
        if(left==0){ReviewRegistry.expire(id);return;}
        if(active.autoSubmit)countdown.setText(Math.max(0,(active.deadlineElapsed-now+999)/1000)+" 秒后自动提交 · 可修改接管");
        else countdown.setText(left==-2?"题目时限待确认 · 暂不能提交":left==-1?"手动提交 · 题目不限时":"手动提交 · 题目剩余 "+((left+999)/1000)+" 秒");
        boolean pending="处理中".equals(active.modelState)||"重试中".equals(active.modelState);
        long elapsed=pending?Math.max(0,now-active.modelStartedElapsed):active.modelDuration;
        modelStatus.setText("模型"+active.modelState+" · "+elapsed/1000+" 秒\n"+active.modelMessage);
        suggestions.setText(active.suggestions.length()==0?"模型建议尚未生成":"模型建议："+join(active.suggestions));
        adopt.setEnabled(active.suggestions.length()>0);retry.setEnabled(!pending&&!"题面待补全".equals(active.modelState));
        retry.setText(active.modelState.startsWith("题面")?"重新获取题面":"重试模型");
        if(!active.touched&&!shownAnswers.equals(active.answers.toString()))syncControls(active.answers);
        validateInput();handler.postDelayed(this,250);
    }};
    @Override protected void onCreate(Bundle b){super.onCreate(b);load(getIntent());}
    @Override protected void onNewIntent(Intent i){super.onNewIntent(i);setIntent(i);load(i);}
    private void load(Intent intent){
        handler.removeCallbacks(ticker);id=intent.getStringExtra("review_id");review=ReviewRegistry.get(id);
        if(review==null){Toast.makeText(this,"该题已结束，请在历史题目查看",Toast.LENGTH_LONG).show();finish();return;}
        inputs.clear();options.clear();keys.clear();initializing=true;
        problemSignature=review.problem.toString();
        LinearLayout root=Ui.screen(this,"课堂题目",review.course+" · 收到后即可手动作答");
        ScrollView scroll=new ScrollView(this);LinearLayout content=Ui.column(this);scroll.addView(content);root.addView(scroll,new LinearLayout.LayoutParams(-1,0,1));
        LinearLayout info=Ui.card(this);countdown=Ui.text(this,"",18,Ui.BLUE,true);info.addView(countdown);Ui.gap(this,info,10);
        JSONObject p=review.problem;String body=p.optString("body","");
        if(body.isEmpty())body=p.optString("content","");if(body.isEmpty())body=p.optString("title","");
        info.addView(Ui.text(this,body.isEmpty()?"已收到题目通知，题面尚未完整获取。":plain(body),16,Ui.INK,false));
        JSONArray opts=p.optJSONArray("options");if(opts==null)opts=new JSONArray();
        boolean images=body.toLowerCase(Locale.ROOT).contains("<img")||p.has("images");
        if(images){Ui.gap(this,info,8);info.addView(Ui.text(this,"本题包含图片，请在雨课堂核对原图。",12,Ui.MUTED,false));}
        content.addView(info);
        LinearLayout answer=Ui.card(this);answer.addView(Ui.text(this,"我的答案",18,Ui.INK,true));Ui.gap(this,answer,8);
        JSONArray saved=review.answers;
        if(review.type==1||review.type==2){
            RadioGroup radios=review.type==1?new RadioGroup(this):null;if(radios!=null){radios.setOrientation(RadioGroup.VERTICAL);answer.addView(radios);}
            for(int i=0;i<opts.length();i++){
                JSONObject opt=opts.optJSONObject(i);String key=opt==null?String.valueOf((char)('A'+i)):opt.optString("key",String.valueOf((char)('A'+i)));
                String text=opt==null?String.valueOf(opts.opt(i)):opt.optString("value",opt.optString("content",""));
                CompoundButton choice=review.type==1?new RadioButton(this):new CheckBox(this);
                choice.setText(key+". "+plain(text));choice.setTextColor(Ui.INK);choice.setTextSize(15);choice.setMinHeight(Ui.dp(this,48));
                choice.setId(View.generateViewId());choice.setChecked(contains(saved,key));options.add(choice);keys.add(key.toUpperCase(Locale.ROOT));
                if(radios!=null)radios.addView(choice);else answer.addView(choice);
                choice.setOnCheckedChangeListener((b,checked)->changed());
            }
        }else if(review.type==4||review.type==5){
            JSONArray blanks=p.optJSONArray("blanks");int count=review.type==5?1:Math.max(1,blanks==null?saved.length():blanks.length());
            for(int i=0;i<count;i++){
                EditText input=Ui.input(this,review.type==4?"填写答案":"填写简答",false);
                if(review.type==5){input.setSingleLine(false);input.setMinLines(3);input.setGravity(Gravity.TOP);}
                input.setText(saved.optString(i,""));inputs.add(input);Ui.field(this,answer,review.type==4?"第 "+(i+1)+" 空":"答案",input);
                input.addTextChangedListener(new TextWatcher(){public void beforeTextChanged(CharSequence s,int st,int count,int after){}public void onTextChanged(CharSequence s,int st,int before,int count){changed();}public void afterTextChanged(Editable e){}});
            }
        }else answer.addView(Ui.text(this,"题型尚未识别或暂不支持，请重新获取题面或在雨课堂作答。",14,Ui.MUTED,false));
        validation=Ui.text(this,"",12,Ui.MUTED,false);Ui.gap(this,answer,10);answer.addView(validation);content.addView(answer);
        LinearLayout model=Ui.card(this);model.addView(Ui.text(this,"AI 建议",18,Ui.INK,true));
        modelStatus=Ui.text(this,"",14,Ui.MUTED,false);Ui.gap(this,model,8);model.addView(modelStatus);
        suggestions=Ui.text(this,"",15,Ui.INK,false);suggestions.setTextIsSelectable(true);Ui.gap(this,model,8);model.addView(suggestions);
        adopt=Ui.button(this,"采用模型建议",false);retry=Ui.button(this,"重试模型",false);Ui.buttonGap(this,model,adopt);Ui.buttonGap(this,model,retry);content.addView(model);
        adopt.setOnClickListener(v->{ReviewRegistry.touch(id);if(ReviewRegistry.update(id,review.suggestions))syncControls(review.answers);validateInput();});
        retry.setOnClickListener(v->{try{startService(new Intent(this,MonitorService.class).setAction("cn.rainclassroom.assistant.RETRY").putExtra("review_id",id));
            Toast.makeText(this,"已请求重试；题面更新后会刷新",Toast.LENGTH_SHORT).show();}catch(Exception e){Toast.makeText(this,"请先启动监听",Toast.LENGTH_SHORT).show();}});
        LinearLayout actions=new LinearLayout(this);Button cancel=Ui.button(this,"取消本题",false);submit=Ui.button(this,"立即提交",true);
        LinearLayout.LayoutParams left=new LinearLayout.LayoutParams(0,Ui.dp(this,48),1);left.rightMargin=Ui.dp(this,8);actions.addView(cancel,left);actions.addView(submit,new LinearLayout.LayoutParams(0,Ui.dp(this,48),1));Ui.gap(this,root,8);root.addView(actions);setContentView(root);
        cancel.setOnClickListener(v->{ReviewRegistry.cancel(id);finish();});
        submit.setOnClickListener(v->{JSONArray values=collect();if(values!=null&&ReviewRegistry.update(id,values)&&ReviewRegistry.submit(id)){
            Toast.makeText(this,"正在提交答案，可在历史题目查看结果",Toast.LENGTH_SHORT).show();finish();
        }else validateInput();});
        initializing=false;shownAnswers=saved.toString();validateInput();handler.post(ticker);
    }
    private static String plain(String s){return Html.fromHtml(s==null?"":s,Html.FROM_HTML_MODE_COMPACT).toString().trim();}
    private static String join(JSONArray a){List<String> out=new ArrayList<>();for(int i=0;i<a.length();i++)out.add(a.optString(i));return android.text.TextUtils.join("、",out);}
    private static boolean contains(JSONArray a,String key){for(int i=0;i<a.length();i++)if(key.equalsIgnoreCase(a.optString(i)))return true;return false;}
    private JSONArray collectDraft(){
        JSONArray out=new JSONArray();
        if(review.type==1||review.type==2){for(int i=0;i<options.size();i++)if(options.get(i).isChecked())out.put(keys.get(i));}
        else if(review.type==4||review.type==5)for(EditText e:inputs)out.put(e.getText().toString());
        return out;
    }
    private JSONArray collect(){
        JSONArray draft=collectDraft(),out=new JSONArray();
        for(int i=0;i<draft.length();i++)out.put(draft.optString(i).trim());
        return ReviewRegistry.validForProblem(review.type,review.problem,out)?out:null;
    }
    private void syncControls(JSONArray values){
        initializing=true;
        for(int i=0;i<options.size();i++)options.get(i).setChecked(contains(values,keys.get(i)));
        for(int i=0;i<inputs.size();i++)inputs.get(i).setText(values.optString(i,""));
        shownAnswers=values.toString();initializing=false;
    }
    private void validateInput(){
        if(submit==null)return;boolean valid=collect()!=null&&!review.ended()&&(review.questionDeadlineElapsed==-1||SystemClock.elapsedRealtime()<review.questionDeadlineElapsed);
        submit.setEnabled(valid);validation.setText(valid?(review.autoSubmit?"可立即提交，修改后会暂停本题自动提交。":"已选答案，可手动提交。"):"请先选择或填写完整答案。");
    }
    private void changed(){if(initializing)return;ReviewRegistry.updateDraft(id,collectDraft());validateInput();}
    @Override protected void onDestroy(){handler.removeCallbacks(ticker);super.onDestroy();}
}

