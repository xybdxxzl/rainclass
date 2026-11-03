package cn.rainclassroom.assistant;

import org.json.*;
import org.junit.*;
import java.util.concurrent.atomic.AtomicInteger;
import static org.junit.Assert.*;

/** 模拟进程重启：仅剩持久化快照，原待答注册表已不存在。 */
public class StoredSubmissionTest {
    @After public void cleanup(){ReviewRegistry.setListener(null);ReviewRegistry.cancelAll();}
    private static JSONObject stored(String state)throws Exception{
        return new JSONObject().put("account","用户1").put("lessonId","课次1").put("problemId","题1").put("state",state)
            .put("answers",new JSONArray().put("A")).put("modelState","成功");
    }
    private static JSONObject problem()throws Exception{
        return new JSONObject().put("body","题目").put("options",new JSONArray().put(new JSONObject().put("key","A")));
    }
    @Test public void restartUnknownResultNeitherCreatesPendingReviewNorOverwritesHistory()throws Exception{
        JSONObject saved=stored("提交结果未知");String original=saved.toString();
        AtomicInteger writes=new AtomicInteger(),submissions=new AtomicInteger();
        ReviewRegistry.setListener(r->writes.incrementAndGet());
        ReviewRegistry.Review r=ReviewRegistry.receivePending("用户1","课次1","课","题1",1,problem(),-1,saved,(a,b)->submissions.incrementAndGet());
        assertNull(r);assertTrue(ReviewRegistry.list().isEmpty());assertEquals(0,writes.get());assertEquals(0,submissions.get());
        assertEquals(original,saved.toString());assertEquals("提交结果未知",saved.getString("state"));
    }
    @Test public void allSentStatesStayReadOnlyWhileOtherAccountsAndUnsentFailuresRemainIndependent()throws Exception{
        for(String state:new String[]{"提交成功","已作答","提交中","提交结果未知"}){
            assertTrue(ReviewRegistry.storedReadOnly(stored(state),"用户1","课次1","题1"));
            assertNull(ReviewRegistry.receivePending("用户1","课次1","课","题1",1,problem(),-1,stored(state),(a,b)->fail("已发送记录不得重新提交")));
        }
        assertFalse(ReviewRegistry.storedReadOnly(stored("提交结果未知"),"用户2","课次1","题1"));
        assertFalse(ReviewRegistry.storedReadOnly(stored("提交结果未知"),"用户1","课次2","题1"));
        JSONObject unsent=stored("待作答");unsent.put("modelState","超时");
        ReviewRegistry.Review manual=ReviewRegistry.receivePending("用户1","课次1","课","题1",1,problem(),-1,unsent,(a,b)->{});
        assertNotNull(manual);assertTrue(ReviewRegistry.update(manual.id,new JSONArray().put("A")));assertTrue(ReviewRegistry.submit(manual.id));
    }
}

