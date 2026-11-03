package cn.rainclassroom.assistant;

import org.json.*;
import org.junit.Test;
import static org.junit.Assert.*;

public class ModelClientTest {
    private static JSONObject question()throws Exception{return new JSONObject().put("options",new JSONArray()
        .put(new JSONObject().put("key","A")).put(new JSONObject().put("key","B")));}
    @Test public void fullEndpointUsesSameNormalizationAsClassroomRequest(){
        assertEquals("https://example.test/v1",ModelClient.normalize(" https://example.test/v1/chat/completions/// "));
    }
    @Test public void connectionTestAndClassroomResponsesRequireValidChoices()throws Exception{
        assertEquals("B",ModelClient.parse("{\"choices\":[\"b\"]}",1,question()).getString(0));
        try{ModelClient.parse("{\"choices\":[\"C\"]}",1,question());fail("unknown choice");}catch(ModelClient.Failure expected){assertEquals("失败",expected.state);}
        try{ModelClient.parse("not json",1,question());fail("invalid format");}catch(ModelClient.Failure expected){assertTrue(expected.getMessage().contains("格式"));}
    }
    @Test public void errorsAreActionableWithoutRawProviderResponses(){
        assertTrue(ModelClient.status(401).getMessage().contains("鉴权"));assertFalse(ModelClient.status(401).retry);
        assertTrue(ModelClient.status(404).getMessage().contains("地址或模型"));assertTrue(ModelClient.status(429).retry);
        assertTrue(ModelClient.status(503).retry);assertFalse(ModelClient.status(400).retry);
    }
    @Test public void subjectAndBlankAnswersAreValidated()throws Exception{
        assertEquals("文字答案",ModelClient.parse("{\"answer\":\"文字答案\"}",5,new JSONObject()).getString(0));
        JSONObject blanks=new JSONObject().put("blanks",new JSONArray().put("").put(""));
        try{ModelClient.parse("{\"blanks\":[\"甲\"]}",4,blanks);fail("blank count");}catch(ModelClient.Failure expected){assertTrue(expected.getMessage().contains("空数"));}
    }
}

