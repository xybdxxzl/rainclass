package cn.rainclassroom.assistant;

import org.json.*;
import org.junit.*;
import java.util.concurrent.atomic.AtomicInteger;
import static org.junit.Assert.*;

public class ImmediateQuestionTest {
    @After public void cleanup(){ReviewRegistry.setListener(null);ReviewRegistry.cancelAll();}
    private static JSONObject problem()throws Exception{return new JSONObject().put("body","请选择").put("problemType",1).put("options",new JSONArray()
        .put(new JSONObject().put("key","A").put("value","甲")).put(new JSONObject().put("key","B").put("value","乙")));}
    private static JSONArray array(String...a){JSONArray out=new JSONArray();for(String v:a)out.put(v);return out;}
    private static ReviewRegistry.Review received(ReviewRegistry.Completion completion)throws Exception{return ReviewRegistry.receive("用户1","课次1","课程","题1",1,problem(),-1,completion);}
    @Test public void modelFailureStillAllowsManualSubmission()throws Exception{
        AtomicInteger submissions=new AtomicInteger();
        ReviewRegistry.Review r=received((review,cancelled)->{if(!cancelled)submissions.incrementAndGet();});
        assertNotNull(ReviewRegistry.get(r.id));assertEquals(0,r.answers.length());assertFalse(ReviewRegistry.submit(r.id));
        ReviewRegistry.modelState(r,"超时","30 秒超时");
        assertTrue(ReviewRegistry.update(r.id,array("B")));assertTrue(ReviewRegistry.submit(r.id));
        assertEquals(1,submissions.get());assertFalse(ReviewRegistry.submit(r.id));assertEquals("提交中",r.submitState);
    }
    @Test public void lateModelCannotOverwriteManualChoice()throws Exception{
        ReviewRegistry.Review r=received((a,b)->{});
        assertTrue(ReviewRegistry.beginModel(r));assertFalse(ReviewRegistry.beginModel(r));
        assertTrue(ReviewRegistry.update(r.id,array("B")));
        assertTrue(ReviewRegistry.applyModel(r.id,array("A"),Long.MAX_VALUE,true));
        assertEquals("B",r.answers.getString(0));assertEquals("A",r.suggestions.getString(0));assertFalse(r.autoSubmit);
    }
    @Test public void emptyDraftSurvivesAndPreventsAutomaticSubmission()throws Exception{
        ReviewRegistry.Review r=received((a,b)->{});
        assertTrue(ReviewRegistry.updateDraft(r.id,array()));
        assertTrue(ReviewRegistry.applyModel(r.id,array("A"),Long.MAX_VALUE,true));
        assertEquals(0,r.answers.length());assertFalse(r.autoSubmit);assertFalse(ReviewRegistry.submit(r.id));
    }
    @Test public void partialBlanksAreRetainedButNeverSubmitted()throws Exception{
        JSONObject p=new JSONObject().put("body","填写").put("blanks",array("",""));
        ReviewRegistry.Review r=ReviewRegistry.receive("用户1","课次1","课","填空",4,p,-1,(a,b)->{});
        assertTrue(ReviewRegistry.updateDraft(r.id,array("甲","")));
        assertEquals("甲",r.answers.getString(0));assertEquals("",r.answers.getString(1));assertFalse(ReviewRegistry.submit(r.id));
        assertTrue(ReviewRegistry.applyModel(r.id,array("乙","丙"),Long.MAX_VALUE,true));
        assertEquals("甲",r.answers.getString(0));assertEquals("",r.answers.getString(1));
    }
    @Test public void timingUnknownIsDistinctFromExpiryAndMayBeCompleted()throws Exception{
        ReviewRegistry.Review r=ReviewRegistry.receive("用户1","课次1","课","题",1,problem(),-2,(a,b)->{});
        assertTrue(ReviewRegistry.updateDraft(r.id,array("A")));assertFalse(ReviewRegistry.submit(r.id));assertNotNull(ReviewRegistry.get(r.id));
        r.questionDeadlineElapsed=-1;assertTrue(ReviewRegistry.submit(r.id));
    }
    @Test public void submittedRecordRejectsLateModelAndExpiry()throws Exception{
        ReviewRegistry.Review r=received((a,b)->{});
        assertTrue(ReviewRegistry.update(r.id,array("A")));assertTrue(ReviewRegistry.submit(r.id));
        assertFalse(ReviewRegistry.applyModel(r.id,array("B"),Long.MAX_VALUE,true));
        assertFalse(ReviewRegistry.expire(r.id));assertEquals("提交中",r.submitState);
    }
    @Test public void questionsFromDifferentAccountsAndLessonsStayIndependent()throws Exception{
        ReviewRegistry.Review a=received((r,c)->{});
        ReviewRegistry.Review b=ReviewRegistry.receive("用户2","课次1","课","题1",1,problem(),-1,(r,c)->{});
        ReviewRegistry.Review c=ReviewRegistry.receive("用户1","课次2","课","题1",1,problem(),-1,(r,d)->{});
        assertNotEquals(a.historyKey(),b.historyKey());assertNotEquals(a.historyKey(),c.historyKey());assertEquals(3,ReviewRegistry.list().size());
        ReviewRegistry.update(a.id,array("B"));assertEquals(0,b.answers.length());assertEquals(0,c.answers.length());
    }
    @Test public void historySnapshotIncludesFailuresAndNoCredentials()throws Exception{
        ReviewRegistry.Review r=received((a,b)->{});ReviewRegistry.modelState(r,"失败","鉴权失败");
        JSONObject stored=HistoryStore.snapshot(r);
        assertEquals("用户1",stored.getString("account"));assertEquals("失败",stored.getString("modelState"));
        assertEquals("待作答",stored.getString("state"));assertEquals("请选择",stored.getJSONObject("problem").getString("body"));
        assertFalse(stored.has("api_key"));assertFalse(stored.has("sessionid"));
    }
}

