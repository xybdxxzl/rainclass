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
        public volatile JSONObject problem;
        public final long receivedAt=System.currentTimeMillis();
        public volatile long questionDeadlineElapsed;
        public volatile JSONArray answers, suggestions=new JSONArray();
        public volatile long deadlineElapsed, modelStartedElapsed, modelDuration;
        public volatile String modelState="未请求", modelMessage="可立即手动作答", submitState="待作答";
        public volatile boolean autoSubmit, touched;
        private final Completion completion;
        private boolean finished;
        private long modelAttempt;
        private boolean imageRefreshRequested;
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
    static final class ModelRequest {
        final JSONObject problem;
        final int type;
        final long attempt;

        private ModelRequest(Review review) {
            problem = review.problem;
            type = review.type;
            attempt = review.modelAttempt;
        }
    }

    static boolean readyForModel(int type, JSONObject problem) {
        return (type == 1 || type == 2 || type == 4 || type == 5)
                && QuestionContent.hasContent(problem) && !problem.optBoolean("_identity_missing")
                && ((type != 1 && type != 2) || QuestionContent.hasOptions(problem));
    }

    /** 合并晚到的题面并撤销依赖旧图片的模型结果和自动提交。 */
    static boolean refreshProblem(Review review, JSONObject cached, JSONObject pushed) {
        boolean startModel;
        synchronized (review) {
            if (!editable(review)) return false;
            JSONObject previous = review.problem;
            JSONObject problem = QuestionContent.merge(QuestionContent.merge(cached, previous), pushed);
            int type = problem.optInt("problemType", review.type);
            boolean wasReady = readyForModel(review.type, previous);
            boolean ready = readyForModel(type, problem);
            if (previous.toString().equals(problem.toString()) && type == review.type) return false;
            boolean imagesChanged = !new QuestionContent(previous).imageReferences()
                    .equals(new QuestionContent(problem).imageReferences());
            review.problem = problem;
            review.type = type;
            if (imagesChanged || (wasReady && !ready)) {
                review.modelAttempt++;
                review.suggestions = new JSONArray();
                review.autoSubmit = false;
                if (!review.touched) review.answers = new JSONArray();
                review.modelState = "未请求";
                review.modelMessage = "题目图片已更新";
                review.modelStartedElapsed = review.modelDuration = 0;
            }
            startModel = ready && (!wasReady || imagesChanged);
        }
        changed(review);
        return startModel;
    }

    private static boolean modelProcessing(Review review) {
        return "处理中".equals(review.modelState) || "重试中".equals(review.modelState);
    }

    static boolean modelActive(Review review, ModelRequest request) {
        synchronized (review) {
            return editable(review) && review.modelAttempt == request.attempt && modelProcessing(review);
        }
    }

    static ModelRequest startModel(Review review) {
        ModelRequest request;
        synchronized (review) {
            if (!editable(review) || modelProcessing(review)) return null;
            review.modelAttempt++;
            review.modelState = "处理中";
            review.modelMessage = "可先选择或填写答案";
            review.modelStartedElapsed = SystemClock.elapsedRealtime();
            request = new ModelRequest(review);
        }
        changed(review);
        return request;
    }

    public static boolean beginModel(Review review) {
        return startModel(review) != null;
    }

    public static boolean applyModel(String id, JSONArray answers, long autoDeadline, boolean allowAuto) {
        Review review = get(id);
        return review != null && applyModel(review, null, answers, autoDeadline, allowAuto);
    }

    static boolean applyModel(Review review, ModelRequest request, JSONArray answers, long autoDeadline, boolean allowAuto) {
        synchronized (review) {
            if (!editable(review) || (request != null && !modelActive(review, request))
                    || !validForProblem(review.type, review.problem, answers)) return false;
            review.suggestions = copy(answers);
            review.modelState = "成功";
            review.modelMessage = "模型建议已生成";
            review.modelDuration = Math.max(0, SystemClock.elapsedRealtime() - review.modelStartedElapsed);
            if (!review.touched) {
                review.answers = copy(answers);
                review.deadlineElapsed = autoDeadline;
                review.autoSubmit = allowAuto && review.questionDeadlineElapsed != -2
                        && autoDeadline > SystemClock.elapsedRealtime();
            }
        }
        changed(review);
        return true;
    }

    static boolean requestImageRefresh(Review review, ModelRequest request) {
        synchronized (review) {
            if (!editable(review) || review.modelAttempt != request.attempt || review.imageRefreshRequested) return false;
            review.imageRefreshRequested = true;
            return true;
        }
    }

    public static void modelState(Review review, String state, String message) {
        modelState(review, null, state, message);
    }

    static boolean modelState(Review review, ModelRequest request, String state, String message) {
        synchronized (review) {
            if (review.finished || (request != null && !modelActive(review, request))) return false;
            review.modelState = state;
            review.modelMessage = message;
            long now = SystemClock.elapsedRealtime();
            if ("处理中".equals(state)) review.modelStartedElapsed = now;
            else if (!"重试中".equals(state)) {
                review.modelDuration = review.modelStartedElapsed > 0 ? Math.max(0, now - review.modelStartedElapsed) : 0;
            }
        }
        changed(review);
        return true;
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

