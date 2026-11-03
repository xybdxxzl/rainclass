package cn.rainclassroom.assistant;

import android.os.SystemClock;
import org.json.*;
import okhttp3.*;
import java.io.*;
import java.net.*;
import java.util.concurrent.TimeUnit;
import java.util.function.*;

/** 连接测试与课堂答题共用请求、超时、重试及答案验证。禁止输出密钥和原始认证错误正文。 */
public final class ModelClient {
    private static final MediaType JSON=MediaType.get("application/json; charset=utf-8");
    public static final class Failure extends IOException {
        public final String state;public final boolean retry;
        Failure(String state,String message,boolean retry){super(message);this.state=state;this.retry=retry;}
    }
    private ModelClient(){}
    public static String normalize(String base){String out=base.trim().replaceAll("/+$","");return out.endsWith("/chat/completions")?out.substring(0,out.length()-17):out;}
    public static JSONArray complete(OkHttpClient http,String base,String key,String model,JSONArray content,int type,JSONObject problem,
                                    long deadline,BooleanSupplier active,Consumer<String> onRetry)throws Exception{
        base=normalize(base);URI endpoint;
        try{endpoint=new URI(base+"/chat/completions");}catch(Exception e){throw new Failure("失败","模型地址无效",false);}
        if(!"https".equalsIgnoreCase(endpoint.getScheme())||endpoint.getHost()==null||endpoint.getUserInfo()!=null||endpoint.getQuery()!=null||endpoint.getFragment()!=null)
            throw new Failure("失败","模型接口必须是有效 HTTPS 地址",false);
        if(key.trim().isEmpty()||model.trim().isEmpty()||key.matches(".*\\s+.*")||model.matches(".*\\s+.*"))throw new Failure("失败","请填写有效的 API Key 和模型名称",false);
        String system="你是一名答题助手，根据题目和选项作答，只输出 JSON。单选或多选：{\"choices\":[\"A\"]}；填空：{\"blanks\":[\"答案\"]}；简答：{\"answer\":\"答案\"}。图片属于题目的一部分。";
        JSONObject payload=new JSONObject().put("model",model.trim()).put("temperature",0).put("messages",new JSONArray()
            .put(new JSONObject().put("role","system").put("content",system)).put(new JSONObject().put("role","user").put("content",content)));
        Request request=new Request.Builder().url(endpoint.toURL()).header("Authorization","Bearer "+key.trim()).post(RequestBody.create(payload.toString(),JSON)).build();
        Exception last=null;
        for(int attempt=0;attempt<3;attempt++){
            if(!active.getAsBoolean())throw new Failure("已停止","请求已取消",false);
            long remaining=deadline-SystemClock.elapsedRealtime();if(remaining<=0)throw new Failure("超时","模型已超过总等待上限",false);
            try{
                OkHttpClient client=http.newBuilder().callTimeout(Math.max(1,remaining),TimeUnit.MILLISECONDS).build();
                try(Response response=client.newCall(request).execute()){
                    if(!response.isSuccessful())throw status(response.code());
                    if(response.body()==null)throw new Failure("失败","模型返回为空",false);
                    String raw=response.body().string();
                    if(SystemClock.elapsedRealtime()>=deadline)throw new Failure("超时","模型已超过总等待上限",false);
                    JSONObject result=new JSONObject(raw);
                    String text=result.getJSONArray("choices").getJSONObject(0).getJSONObject("message").optString("content","");
                    return parse(text,type,problem);
                }
            }catch(Failure e){last=e;if(!e.retry)throw e;}
            catch(JSONException e){throw new Failure("失败","模型返回格式异常",false);}
            catch(InterruptedIOException e){last=new Failure("超时","模型响应超时",true);}
            catch(IOException e){last=new Failure("失败","模型网络连接失败",true);}
            long pause=(attempt+1)*1000L;
            if(attempt==2||deadline-SystemClock.elapsedRealtime()<=pause+1000)break;
            if(onRetry!=null)onRetry.accept("第 "+(attempt+2)+"/3 次请求，即将重试");
            try{Thread.sleep(pause);}catch(InterruptedException e){Thread.currentThread().interrupt();throw new Failure("已停止","请求已取消",false);}
        }
        if(last!=null)throw last;throw new Failure("失败","模型请求失败",false);
    }
    public static Failure status(int code){
        if(code==401||code==403)return new Failure("失败","鉴权失败，请检查密钥和模型权限（HTTP "+code+"）",false);
        if(code==404)return new Failure("失败","接口地址或模型不存在（HTTP 404）",false);
        if(code==429)return new Failure("失败","模型请求限流或额度不足（HTTP 429）",true);
        if(code>=500)return new Failure("失败","模型服务暂时不可用（HTTP "+code+"）",true);
        return new Failure("失败","模型接口拒绝请求（HTTP "+code+"）",false);
    }
    static JSONArray parse(String text,int type,JSONObject problem)throws Exception{
        int first=text.indexOf('{'),last=text.lastIndexOf('}');
        if(first<0||last<first)throw new Failure("失败","模型返回格式异常",false);
        JSONObject value;
        try{value=new JSONObject(text.substring(first,last+1));}catch(JSONException e){throw new Failure("失败","模型返回格式异常",false);}
        JSONArray source=type==5?new JSONArray().put(value.optString("answer","").trim()):value.optJSONArray(type==4?"blanks":"choices");
        if(!ReviewRegistry.valid(type,source))throw new Failure("失败","模型未返回有效答案",false);
        JSONArray out=new JSONArray();for(int i=0;i<source.length();i++){String s=source.getString(i).trim();out.put(type==1||type==2?s.toUpperCase(java.util.Locale.ROOT):s);}
        if(!ReviewRegistry.validForProblem(type,problem,out))throw new Failure("失败","模型答案与题目选项或空数不匹配",false);
        return out;
    }
    public static void test(String base,String key,String model,int seconds)throws Exception{
        JSONObject p=new JSONObject().put("body","请选择字母 A").put("options",new JSONArray().put(new JSONObject().put("key","A").put("value","A")));
        JSONArray content=new JSONArray().put(new JSONObject().put("type","text").put("text","请选择 A，只输出 {\"choices\":[\"A\"]}。"));
        OkHttpClient http=new OkHttpClient.Builder().connectTimeout(10,TimeUnit.SECONDS).followSslRedirects(false).build();
        try{complete(http,base,key,model,content,1,p,SystemClock.elapsedRealtime()+Math.min(30,Math.max(5,seconds))*1000L,()->true,null);}
        finally{http.dispatcher().cancelAll();http.connectionPool().evictAll();http.dispatcher().executorService().shutdown();}
    }
}

