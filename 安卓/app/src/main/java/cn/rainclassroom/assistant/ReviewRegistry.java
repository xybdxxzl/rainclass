package cn.rainclassroom.assistant;

import android.os.SystemClock;
import org.json.*;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;

/** 收题即建立可编辑记录。模型建议与人工答案独立，所有完成动作互斥。 */
public final class ReviewRegistry {
    public interface Completion { void finished(Review review, boolean cancelled); }
    public interface Listener { void changed(Review review); }
    private static volatile Listener listener;
    public static void setListener(Listener value) { listener=value; }
    private static void changed(Review r) { Listener l=listener;if(l!=null)l.changed(r); }
    public static final class Review {
        public final String id, account, lessonId, course, problemId;
        public volatile int type;
        public final JSONObject problem;
        public final long receivedAt=System.currentTimeMillis();
        public volatile long questionDeadlineElapsed;
        public volatile JSONArray answers, suggestions=new JSONArray();
        public volatile long deadlineElapsed, modelStartedElapsed, modelDuration;
        public volatile String modelState="未请求", modelMessage="可立即手动作答", submitState="待作答";
        public volatile boolean autoSubmit, touched;
        private final Completion completion;
        private boolean finished;
        private Review(String account,String lessonId,String course,String problemId,int type,JSONObject problem,
                       JSONArray answers,long deadline,long questionDeadline,Completion completion) {
            this.id=UUID.randomUUID().toString();this.account=account;this.lessonId=lessonId;this.course=course;
            this.problemId=problemId;this.type=type;this.problem=problem;this.answers=copy(answers);
            this.deadlineElapsed=deadline;this.questionDeadlineElapsed=questionDeadline;this.completion=completion;
        }
        public String historyKey(){return account+":"+lessonId+":"+problemId;}
        public boolean ended(){synchronized(this){return finished;}}
    }
    private static final ConcurrentHashMap<String,Review> REVIEWS=new ConcurrentHashMap<>();
    private ReviewRegistry(){}
    static JSONArray copy(JSONArray source){try{return new JSONArray(source==null?"[]":source.toString());}catch(JSONException e){throw new IllegalArgumentException(e);}}
    public static Review receive(String account,String lessonId,String course,String problemId,int type,JSONObject problem,
                                 long questionDeadline,Completion completion){
        Review r=new Review(account,lessonId,course,problemId,type,problem,new JSONArray(),Long.MAX_VALUE,questionDeadline,completion);
        REVIEWS.put(r.id,r);changed(r);return r;
    }
    /** 已发送过或结果未知的题目跨重启仍只读，避免 listener 覆写原记录后再次提交。 */
    public static boolean storedReadOnly(JSONObject stored,String account,String lessonId,String problemId){
        if(stored==null||!account.equals(stored.optString("account"))||!lessonId.equals(stored.optString("lessonId"))
            ||!problemId.equals(stored.optString("problemId")))return false;
        String state=stored.optString("state");
        return "提交成功".equals(state)||"已作答".equals(state)||"提交中".equals(state)||"提交结果未知".equals(state);
    }
    public static Review receivePending(String account,String lessonId,String course,String problemId,int type,JSONObject problem,
                                        long questionDeadline,JSONObject stored,Completion completion){
        if(storedReadOnly(stored,account,lessonId,problemId))return null;
        return receive(account,lessonId,course,problemId,type,problem,questionDeadline,completion);
    }
    public static Review create(String course,String problemId,int type,JSONObject problem,JSONArray answers,long deadline,long questionDeadline,Completion completion){
        if(!validForProblem(type,problem,answers))throw new IllegalArgumentException("答案为空或格式有误");
        Review r=new Review("","",course,problemId,type,problem,answers,deadline,questionDeadline,completion);
        r.autoSubmit=true;REVIEWS.put(r.id,r);changed(r);return r;
    }
    public static List<Review> list(){List<Review> out=new ArrayList<>(REVIEWS.values());out.sort(Comparator.comparingLong(r->r.receivedAt));return out;}
    public static Review get(String id){return id==null?null:REVIEWS.get(id);}
    public static boolean valid(int type,JSONArray answers){
        if(type!=1&&type!=2&&type!=4&&type!=5)return false;
        if(answers==null||answers.length()==0||((type==1||type==5)&&answers.length()!=1))return false;
        for(int i=0;i<answers.length();i++)if(!(answers.opt(i) instanceof String)||answers.optString(i).trim().isEmpty())return false;
        return true;
    }
    public static boolean validForProblem(int type,JSONObject problem,JSONArray answers){
        if(problem.optBoolean("_identity_missing",false)||!valid(type,answers))return false;
        if(type==1||type==2){
            JSONArray options=problem.optJSONArray("options");if(options==null||options.length()==0)return false;
            Set<String> allowed=new HashSet<>(),used=new HashSet<>();
            for(int i=0;i<options.length();i++){JSONObject o=options.optJSONObject(i);allowed.add((o==null?String.valueOf((char)('A'+i)):o.optString("key",String.valueOf((char)('A'+i)))).toUpperCase(Locale.ROOT));}
            for(int i=0;i<answers.length();i++){String a=answers.optString(i).trim().toUpperCase(Locale.ROOT);if(!allowed.contains(a)||!used.add(a))return false;}
        }else if(type==4){JSONArray blanks=problem.optJSONArray("blanks");if(blanks!=null&&blanks.length()>0&&answers.length()!=blanks.length())return false;}
        return true;
    }
    private static boolean editable(Review r){return !r.finished&&(r.questionDeadlineElapsed==-1||r.questionDeadlineElapsed==-2||SystemClock.elapsedRealtime()<r.questionDeadlineElapsed);}
    public static boolean update(String id,JSONArray answers){
        Review r=get(id);if(r==null||!validForProblem(r.type,r.problem,answers))return false;
        synchronized(r){if(!editable(r)||(r.autoSubmit&&SystemClock.elapsedRealtime()>=r.deadlineElapsed))return false;
            r.answers=copy(answers);r.touched=true;r.autoSubmit=false;}
        changed(r);return true;
    }
    public static boolean updateDraft(String id,JSONArray answers){
        Review r=get(id);if(r==null||answers==null)return false;
        synchronized(r){if(!editable(r))return false;r.answers=copy(answers);r.touched=true;r.autoSubmit=false;}
        changed(r);return true;
    }
    /** 暂时为空的输入也算人工接管，避免模型覆盖正在编辑的内容。 */
    public static void touch(String id){Review r=get(id);if(r==null)return;synchronized(r){if(!editable(r))return;r.touched=true;r.autoSubmit=false;}changed(r);}
    public static boolean beginModel(Review r){
        synchronized(r){if(!editable(r)||"处理中".equals(r.modelState)||"重试中".equals(r.modelState))return false;
            r.modelState="处理中";r.modelMessage="可先选择或填写答案";r.modelStartedElapsed=SystemClock.elapsedRealtime();}
        changed(r);return true;
    }
    public static boolean applyModel(String id,JSONArray answers,long autoDeadline,boolean allowAuto){
        Review r=get(id);if(r==null||!validForProblem(r.type,r.problem,answers))return false;
        synchronized(r){if(!editable(r))return false;r.suggestions=copy(answers);r.modelState="成功";r.modelMessage="模型建议已生成";
            r.modelDuration=Math.max(0,SystemClock.elapsedRealtime()-r.modelStartedElapsed);
            if(!r.touched){r.answers=copy(answers);r.deadlineElapsed=autoDeadline;r.autoSubmit=allowAuto&&r.questionDeadlineElapsed!=-2&&autoDeadline>SystemClock.elapsedRealtime();}}
        changed(r);return true;
    }
    public static void modelState(Review r,String state,String message){synchronized(r){if(r.finished)return;r.modelState=state;r.modelMessage=message;
        if("处理中".equals(state))r.modelStartedElapsed=SystemClock.elapsedRealtime();else if(!"重试中".equals(state))r.modelDuration=r.modelStartedElapsed>0?Math.max(0,SystemClock.elapsedRealtime()-r.modelStartedElapsed):0;}
        changed(r);
    }
    public static void submission(Review r,String state){synchronized(r){r.submitState=state;}changed(r);}
    public static boolean submit(String id){return finish(id,false,false,"");}
    public static boolean autoSubmit(String id){return finish(id,false,true,"");}
    public static boolean cancel(String id){return finish(id,true,false,"已取消");}
    public static boolean expire(String id){return finish(id,true,false,"已过期");}
    private static boolean finish(String id,boolean cancelled,boolean automatic,String cancelState){
        Review r=get(id);if(r==null)return false;
        synchronized(r){if(r.finished)return false;
            if(!cancelled&&(r.questionDeadlineElapsed==-2||!editable(r)||!validForProblem(r.type,r.problem,r.answers)))return false;
            if(automatic&&(!r.autoSubmit||SystemClock.elapsedRealtime()<r.deadlineElapsed))return false;
            r.finished=true;if(!"已作答".equals(r.submitState))r.submitState=cancelled?cancelState:"提交中";REVIEWS.remove(r.id,r);}
        changed(r);r.completion.finished(r,cancelled);return true;
    }
    public static void cancelAll(){for(Review r:list())cancel(r.id);}
}

