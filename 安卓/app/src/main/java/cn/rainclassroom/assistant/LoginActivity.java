package cn.rainclassroom.assistant;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.text.InputType;
import android.view.View;
import android.webkit.CookieManager;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.webkit.WebChromeClient;
import android.widget.*;
import org.json.JSONObject;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.IOException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import okhttp3.*;

public class LoginActivity extends Activity {
    private WebView web;
    private TextView status;
    private Button use, manualSubmit;
    private EditText manual;
    private View officialPanel, alternatePanel;
    private Button modeButton;
    private LoginSessionDetector detector;
    private final AtomicBoolean validating = new AtomicBoolean();
    private final Handler main=new Handler(Looper.getMainLooper());
    private final Runnable checkLogin = new Runnable() { @Override public void run() {
        if (destroyed) return;
        if (officialPanel != null && officialPanel.getVisibility() == View.VISIBLE) {
            String token = extractSession(CookieManager.getInstance().getCookie("https://www.yuketang.cn/"));
            if (!validating.get() && detector.shouldCheck(token, SystemClock.elapsedRealtime())) validate(token, true);
        }
        main.postDelayed(this, 2000);
    }};
    private final OkHttpClient qrClient=new OkHttpClient.Builder().connectTimeout(10,TimeUnit.SECONDS)
            .readTimeout(15,TimeUnit.SECONDS).pingInterval(25,TimeUnit.SECONDS).followRedirects(false).followSslRedirects(false).build();
    private volatile WebSocket qrSocket;
    private ImageView qrImage;
    private TextView qrStatus;
    private Button qrSave;
    private Bitmap qrBitmap;
    private final AtomicInteger qrGeneration=new AtomicInteger();
    private final AtomicBoolean exchanging=new AtomicBoolean();
    private final AtomicBoolean connecting=new AtomicBoolean();
    private int qrSaveGeneration=-1;
    private volatile boolean destroyed=false;
    private final Runnable refreshQr=new Runnable(){@Override public void run(){
        if(destroyed)return;requestQr();main.postDelayed(this,60000);
    }};
    @Override protected void onCreate(Bundle b){super.onCreate(b);
        LinearLayout root=Ui.screen(this,"登录雨课堂","请选择手机号短信登录或账号密码登录。");
        LinearLayout qrCard=Ui.card(this);qrCard.addView(Ui.text(this,"微信扫码登录",17,Ui.INK,true));Ui.gap(this,qrCard,4);
        qrCard.addView(Ui.text(this,"用另一台设备扫码，或把二维码保存到图片目录，再用微信从相册识别。二维码会定期刷新。",12,Ui.MUTED,false));
        qrImage=new ImageView(this);qrImage.setScaleType(ImageView.ScaleType.FIT_CENTER);
        LinearLayout.LayoutParams imageParams=new LinearLayout.LayoutParams(Ui.dp(this,200),Ui.dp(this,200));imageParams.gravity=android.view.Gravity.CENTER_HORIZONTAL;
        Ui.gap(this,qrCard,8);qrCard.addView(qrImage,imageParams);
        qrStatus=Ui.text(this,"正在获取二维码…",12,Ui.MUTED,false);Ui.gap(this,qrCard,6);qrCard.addView(qrStatus);
        qrSave=Ui.button(this,"保存二维码到手机",false);qrSave.setEnabled(false);Ui.buttonGap(this,qrCard,qrSave);
        qrSave.setOnClickListener(v->saveQr());
        Button refresh=Ui.button(this,"刷新二维码",false);Ui.buttonGap(this,qrCard,refresh);refresh.setOnClickListener(v->requestQr());
        web=new WebView(this);web.setBackgroundColor(android.graphics.Color.WHITE);
        WebSettings settings=web.getSettings();settings.setJavaScriptEnabled(true);settings.setDomStorageEnabled(true);
        settings.setUseWideViewPort(true);settings.setLoadWithOverviewMode(true);
        settings.setSupportZoom(true);settings.setBuiltInZoomControls(true);settings.setDisplayZoomControls(false);
        settings.setAllowFileAccess(false);settings.setAllowContentAccess(false);settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        CookieManager.getInstance().setAcceptCookie(true);CookieManager.getInstance().setAcceptThirdPartyCookies(web,true);
        detector = new LoginSessionDetector(extractSession(CookieManager.getInstance().getCookie("https://www.yuketang.cn/")),
                getSharedPreferences("rain_config",MODE_PRIVATE).getString("sessionid", ""));
        web.setWebChromeClient(new WebChromeClient());
        web.setWebViewClient(new WebViewClient(){
            @Override public boolean shouldOverrideUrlLoading(WebView view,WebResourceRequest request){return request.isForMainFrame() && handleNavigation(request.getUrl());}
            @SuppressWarnings("deprecation") @Override public boolean shouldOverrideUrlLoading(WebView view,String url){return handleNavigation(Uri.parse(url));}
            @Override public void onPageFinished(WebView view,String address){
                super.onPageFinished(view,address);
                Uri uri=Uri.parse(address);
                if("https".equalsIgnoreCase(uri.getScheme()) && "www.yuketang.cn".equalsIgnoreCase(uri.getHost())
                        && ("/web".equals(uri.getPath()) || "/web/".equals(uri.getPath()))) openSmsTab(view);
            }
        });
        officialPanel = web;
        root.addView(web,new LinearLayout.LayoutParams(-1,0,1));
        use=Ui.button(this,"我已登录",true);
        use.setOnClickListener(v->{String cookie=CookieManager.getInstance().getCookie("https://www.yuketang.cn/");
            String token=extractSession(cookie);if(token.isEmpty()){status.setText("尚未检测到登录状态，请先在官方页面完成登录。");return;}validate(token);});
        LinearLayout manualCard=Ui.card(this);manualCard.addView(Ui.text(this,"已有登录凭据",17,Ui.INK,true));
        manual=Ui.input(this,"粘贴 sessionid 的值",true);manual.setInputType(InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_VARIATION_PASSWORD);
        Ui.field(this,manualCard,"sessionid",manual);manualSubmit=Ui.button(this,"验证并登录",false);Ui.buttonGap(this,manualCard,manualSubmit);
        manualSubmit.setOnClickListener(v->{String token=manual.getText().toString().trim();if(token.startsWith("sessionid="))token=token.substring(10).trim();validate(token);});
        ScrollView scroll=new ScrollView(this);LinearLayout content=Ui.column(this);content.addView(qrCard);content.addView(manualCard);scroll.addView(content);
        alternatePanel=scroll;scroll.setVisibility(View.GONE);root.addView(scroll,new LinearLayout.LayoutParams(-1,0,1));
        status=Ui.text(this,"请选择账号登录或短信登录；完成后会自动识别登录状态。",12,Ui.MUTED,false);
        Ui.gap(this,root,6);root.addView(status);
        LinearLayout actions=new LinearLayout(this);actions.setOrientation(LinearLayout.HORIZONTAL);
        Button back=Ui.button(this,"返回",false);modeButton=Ui.button(this,"其他方式",false);
        for(Button button:new Button[]{back,modeButton,use}){LinearLayout.LayoutParams p=new LinearLayout.LayoutParams(0,Ui.dp(this,48),1);p.setMargins(Ui.dp(this,3),0,Ui.dp(this,3),0);actions.addView(button,p);}
        Ui.gap(this,root,8);root.addView(actions);back.setOnClickListener(v->finish());
        modeButton.setOnClickListener(v->{boolean alternate=alternatePanel.getVisibility()!=View.VISIBLE;
            alternatePanel.setVisibility(alternate?View.VISIBLE:View.GONE);officialPanel.setVisibility(alternate?View.GONE:View.VISIBLE);
            modeButton.setText(alternate?"账号登录":"其他方式");use.setVisibility(alternate?View.GONE:View.VISIBLE);
            status.setText(alternate?"可扫码登录或手动填写凭据。":"请选择账号登录或短信登录；完成后会自动识别登录状态。");
            if(alternate&&qrSocket==null)connectQr();});
        setContentView(root);web.loadUrl("https://www.yuketang.cn/web");main.postDelayed(checkLogin,2000);
    }
    private void openSmsTab(WebView view){
        // 雨课堂公开登录页的现有标签：仅切换入口，不自动接受协议或填写任何信息。
        view.evaluateJavascript("(function(){if(window.__rainSmsChoice)return;window.__rainSmsChoice=true;"
            +"var end=Date.now()+15000;function select(){var tab=document.querySelector('.tab-box .item[data-type=message]');"
            +"var qr=document.querySelector('.mabox.toggle-box[data-type=qrcode] .changeImg');"
            +"var box=document.querySelector('.account-box.toggle-box');"
            +"if(tab&&qr&&box){if(getComputedStyle(box).display==='none')qr.click();tab.click();return;}"
            +"if(Date.now()<end)setTimeout(select,400);}select();})()", null);
    }
    private void connectQr(){
        if(destroyed||!connecting.compareAndSet(false,true))return;
        Request request=new Request.Builder().url("wss://www.yuketang.cn/wsapp/").build();
        qrSocket=qrClient.newWebSocket(request,new WebSocketListener(){
            @Override public void onOpen(WebSocket ws,Response response){
                connecting.set(false);if(destroyed){ws.close(1000,"closed");return;}
                qrSocket=ws;main.post(()->{if(!destroyed){requestQr();main.removeCallbacks(refreshQr);main.postDelayed(refreshQr,60000);}});
            }
            @Override public void onMessage(WebSocket ws,String text){
                if(destroyed)return;
                try{JSONObject data=new JSONObject(text);String op=data.optString("op","");
                    if("requestlogin".equals(op)){String ticket=data.optString("ticket","");if(!ticket.isEmpty())downloadQr(ticket,qrGeneration.get());}
                    else if("loginsuccess".equals(op)&&data.has("UserID")&&data.has("Auth")&&exchanging.compareAndSet(false,true))exchange(data);
                }catch(Exception ignored){postQrStatus("登录信息解析失败，请刷新二维码。");}
            }
            @Override public void onFailure(WebSocket ws,Throwable t,Response response){
                if(ws==qrSocket){connecting.set(false);if(!destroyed)postQrStatus("二维码连接已断开，请点击刷新重连。");}
            }
            @Override public void onClosed(WebSocket ws,int code,String reason){if(!destroyed&&ws==qrSocket)postQrStatus("二维码连接已断开，请点击刷新重连。");}
        });
    }
    private void requestQr(){
        if(destroyed)return;
        qrGeneration.incrementAndGet();qrBitmap=null;qrImage.setImageDrawable(null);qrSave.setEnabled(false);
        if(qrSocket==null || !qrSocket.send("{\"op\":\"requestlogin\",\"role\":\"web\",\"version\":1.4,\"type\":\"qrcode\",\"from\":\"web\"}")){
            qrStatus.setText("正在重新连接二维码服务…");if(qrSocket!=null)qrSocket.cancel();qrSocket=null;connectQr();return;
        }
        qrStatus.setText("正在获取二维码…");
    }
    private void downloadQr(String address,int generation){
        try{
            HttpUrl url=HttpUrl.get(address);
            if(!url.isHttps()||!url.username().isEmpty()||!url.password().isEmpty())throw new IOException("invalid URL");
            Request request=new Request.Builder().url(url).build();
            qrClient.newCall(request).enqueue(new Callback(){
                @Override public void onFailure(Call call,IOException e){postQrStatus("二维码加载失败，请点击刷新。");}
                @Override public void onResponse(Call call,Response response){
                    try(Response r=response){
                        if(!r.isSuccessful()||r.body()==null)throw new IOException("invalid response");
                        byte[] bytes;try(InputStream in=r.body().byteStream();ByteArrayOutputStream out=new ByteArrayOutputStream()){
                            byte[] buf=new byte[4096];int n;while((n=in.read(buf))!=-1){if(out.size()+n>2*1024*1024)throw new IOException("image too large");out.write(buf,0,n);}bytes=out.toByteArray();}
                        BitmapFactory.Options options=new BitmapFactory.Options();options.inJustDecodeBounds=true;BitmapFactory.decodeByteArray(bytes,0,bytes.length,options);
                        if(options.outWidth<=0||options.outHeight<=0||options.outWidth>4096||options.outHeight>4096)throw new IOException("invalid image");
                        Bitmap image=BitmapFactory.decodeByteArray(bytes,0,bytes.length);if(image==null)throw new IOException("decode failed");
                        main.post(()->{if(!destroyed&&generation==qrGeneration.get()){qrBitmap=image;qrImage.setImageBitmap(image);qrSave.setEnabled(true);qrStatus.setText("请使用微信识别二维码并确认登录。");}});
                    }catch(Exception e){postQrStatus("二维码加载失败，请点击刷新。");}
                }
            });
        }catch(Exception e){postQrStatus("二维码地址无效，请点击刷新。");}
    }
    private void exchange(JSONObject message){
        try{
            JSONObject payload=new JSONObject().put("UserID",message.get("UserID")).put("Auth",message.get("Auth"));
            Request request=new Request.Builder().url("https://www.yuketang.cn/pc/web_login")
                    .header("User-Agent","Mozilla/5.0")
                    .post(RequestBody.create(payload.toString(),MediaType.get("application/json; charset=utf-8"))).build();
            postQrStatus("已扫码，正在验证登录…");
            qrClient.newCall(request).enqueue(new Callback(){
                @Override public void onFailure(Call call,IOException e){exchanging.set(false);postQrStatus("登录验证失败，请刷新二维码重试。");}
                @Override public void onResponse(Call call,Response response){
                    try(Response r=response){
                        // 登录接口可能通过重定向携带 Set-Cookie；提取当前可信响应，不跟随跳转。
                        if(!r.isSuccessful()&&(r.code()<300||r.code()>=400))throw new IOException("login failed");
                        String token="";for(String header:r.headers("Set-Cookie")){
                            Cookie cookie=Cookie.parse(r.request().url(),header);
                            if(cookie!=null&&"sessionid".equals(cookie.name())){token=cookie.value();break;}}
                        if(token.isEmpty())throw new IOException("session missing");
                        final String session=token;main.post(()->{if(!destroyed)validate(session);});
                    }catch(Exception e){exchanging.set(false);postQrStatus("登录验证失败，请刷新二维码重试。");}
                }
            });
        }catch(Exception e){exchanging.set(false);postQrStatus("登录验证失败，请刷新二维码重试。");}
    }
    private void postQrStatus(String message){main.post(()->{if(!destroyed&&qrStatus!=null)qrStatus.setText(message);});}
    private void saveQr(){
        if(qrBitmap==null){qrStatus.setText("二维码尚未加载，请稍后再试。");return;}
        qrSaveGeneration=qrGeneration.get();
        Intent intent=new Intent(Intent.ACTION_CREATE_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("image/png")
                .putExtra(Intent.EXTRA_TITLE,"雨课堂登录二维码.png");
        try{startActivityForResult(intent,101);}catch(ActivityNotFoundException e){qrStatus.setText("未找到系统文件保存工具。");}
    }
    @Override protected void onActivityResult(int request,int result,Intent data){super.onActivityResult(request,result,data);
        if(request!=101||result!=RESULT_OK||data==null||data.getData()==null)return;
        Bitmap image=qrBitmap;if(image==null||qrSaveGeneration!=qrGeneration.get()){qrStatus.setText("二维码已刷新，请重新保存。");return;}
        try(OutputStream out=getContentResolver().openOutputStream(data.getData())){
            if(out==null||!image.compress(Bitmap.CompressFormat.PNG,100,out))throw new IOException("save failed");
            qrStatus.setText("二维码已保存，请在微信中从相册识别。");
        }catch(Exception e){qrStatus.setText("二维码保存失败，请重试。");}
    }
    private boolean handleNavigation(Uri uri){
        String scheme=uri.getScheme(), host=uri.getHost();
        if("https".equalsIgnoreCase(scheme) && host!=null){String h=host.toLowerCase(java.util.Locale.ROOT);
            if(h.equals("yuketang.cn")||h.endsWith(".yuketang.cn")||h.equals("xuetangx.com")||h.endsWith(".xuetangx.com")
                    ||h.equals("open.weixin.qq.com"))return false;}
        if("weixin".equalsIgnoreCase(scheme)){
            try{Intent i=new Intent(Intent.ACTION_VIEW,uri);i.setPackage("com.tencent.mm");startActivity(i);}catch(ActivityNotFoundException e){status.setText("未找到微信，请使用扫码或手动登录。");}
        } else status.setText("已阻止跳转到非雨课堂登录页面。");
        return true;
    }
    private static String extractSession(String cookies){
        if(cookies==null)return "";for(String part:cookies.split(";")){String p=part.trim();if(p.startsWith("sessionid="))return p.substring(10).trim();}return "";
    }
    private void validate(String token){ validate(token,false); }
    private void validate(String token, boolean automatic){
        if(!token.matches("[\\x21-\\x3A\\x3C-\\x7E]+")){status.setText("请填写有效的 sessionid 值，不要包含空格或分号。");return;}
        if(!validating.compareAndSet(false,true))return;
        if(!automatic)status.setText("正在验证登录状态…");use.setEnabled(false);manualSubmit.setEnabled(false);
        Request request=new Request.Builder().url("https://www.yuketang.cn/api/v3/user/basic-info")
                .header("Cookie","sessionid="+token).header("User-Agent","Mozilla/5.0").build();
        qrClient.newCall(request).enqueue(new Callback(){
            @Override public void onFailure(Call call,IOException e){loginResult(token,false,automatic);}
            @Override public void onResponse(Call call,Response response){
                boolean ok=false;try(Response r=response){if(r.isSuccessful()&&r.body()!=null){
                    try(InputStream in=r.body().byteStream();ByteArrayOutputStream out=new ByteArrayOutputStream()){
                        byte[] buffer=new byte[4096];int n;while((n=in.read(buffer))!=-1){if(out.size()+n>1048576)throw new IOException("response too large");out.write(buffer,0,n);}
                        ok=new JSONObject(out.toString("UTF-8")).optInt("code",-1)==0;
                    }
                }}catch(Exception ignored){}loginResult(token,ok,automatic);
            }
        });
    }
    private void loginResult(String token,boolean result,boolean automatic){main.post(()->{
        validating.set(false);
        if(isFinishing()||isDestroyed())return;use.setEnabled(true);manualSubmit.setEnabled(true);
        // 异步验证期间登录页可能已切换账号；过期结果不能覆盖新会话。
        if(automatic && !token.equals(extractSession(CookieManager.getInstance().getCookie("https://www.yuketang.cn/"))))return;
        if(result){if(getSharedPreferences("rain_config",MODE_PRIVATE).edit().putString("sessionid",token).commit()){
            setResult(RESULT_OK);Toast.makeText(this,"登录成功",Toast.LENGTH_SHORT).show();finish();}
            else {exchanging.set(false);status.setText("保存失败，请检查设备存储空间。");}}
        else {exchanging.set(false);if(!automatic){status.setText("验证未成功。请确认登录状态有效，并检查手机网络。");qrStatus.setText("登录验证未成功，请刷新二维码重试。");}}
    });}
    @Override public void onBackPressed(){if(web!=null&&web.canGoBack())web.goBack();else super.onBackPressed();}
    @Override protected void onDestroy(){destroyed=true;main.removeCallbacksAndMessages(null);if(qrSocket!=null){qrSocket.cancel();qrSocket=null;}
        qrClient.dispatcher().cancelAll();qrClient.dispatcher().executorService().shutdown();qrClient.connectionPool().evictAll();
        if(web!=null){web.stopLoading();web.destroy();web=null;}super.onDestroy();}
}
