package fr.kerherve.jarvis;

import android.Manifest;
import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.speech.RecognitionListener;
import android.speech.RecognizerIntent;
import android.speech.SpeechRecognizer;
import android.webkit.JavascriptInterface;
import android.webkit.WebSettings;
import android.webkit.WebView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Jarvis — interface (assets/index.html : modes Avatar et Chat, style Kerherve V7) dans une WebView.
 * Le natif gère : appels HA (HaClient), dictée Android, voix (Voice), réglages.
 */
public class MainActivity extends Activity {

    private static final int REQ_MIC = 42, REQ_NOTIF = 43;

    private WebView web;
    private final ExecutorService io = Executors.newCachedThreadPool();
    private final Handler ui = new Handler(Looper.getMainLooper());
    private SpeechRecognizer recognizer;

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        int bg = Color.parseColor("#050A14");
        getWindow().setStatusBarColor(Color.parseColor("#0B1626"));
        getWindow().setNavigationBarColor(bg);

        web = new WebView(this);
        web.setBackgroundColor(bg);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setAllowFileAccess(false);
        web.addJavascriptInterface(new Bridge(), "Android");
        web.loadUrl("file:///android_asset/index.html");
        setContentView(web);

        Voice.init(this);
        Notifs.channels(this);
        if (Build.VERSION.SDK_INT >= 33
                && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, REQ_NOTIF);
    }

    @Override
    protected void onResume() {
        super.onResume();
        Voice.setListener((type, start, end) -> js("onTts('" + type + "'," + start + "," + end + ")"));
    }

    @Override
    protected void onPause() {
        Voice.setListener(null);
        super.onPause();
    }

    @Override
    public void onBackPressed() {
        js("document.body.classList.contains('open')?document.body.classList.remove('open'):0");
    }

    @Override
    protected void onDestroy() {
        if (recognizer != null) recognizer.destroy();
        io.shutdownNow();
        web.destroy();
        super.onDestroy();
    }

    private void js(String code) {
        ui.post(() -> web.evaluateJavascript(code, null));
    }

    private static String q(String s) { return JSONObject.quote(s == null ? "" : s); }

    // ---------------- Pont JS ----------------

    private class Bridge {

        @JavascriptInterface
        public String getConfig() {
            String j = HaClient.prefs(MainActivity.this).getString("cfg_json", null);
            if (j != null) return j;
            try { // migration v1.0
                JSONObject o = new JSONObject();
                o.put("url", HaClient.prefs(MainActivity.this).getString("url", "http://192.168.1.53:8123"));
                o.put("token", HaClient.prefs(MainActivity.this).getString("token", ""));
                o.put("agent", "adria");
                return o.toString();
            } catch (Exception e) { return "{}"; }
        }

        @JavascriptInterface
        public void saveConfig(String json) {
            HaClient.prefs(MainActivity.this).edit().putString("cfg_json", json).apply();
        }

        @JavascriptInterface
        public void ask(String convId, String text) {
            io.execute(() -> {
                HaClient ha = HaClient.from(MainActivity.this);
                try {
                    JSONObject r = ha.ask(text, convId);
                    js("onHaReply(true," + q(r.toString()) + ")");
                } catch (Exception e) {
                    js("onHaReply(false," + q(HaClient.explain(e, ha.url)) + ")");
                }
            });
        }

        @JavascriptInterface
        public void test(String url, String token) {
            io.execute(() -> {
                HaClient ha = HaClient.from(MainActivity.this);
                try {
                    String r = ha.http("GET", url + "/api/", null, token);
                    String msg = new JSONObject(r).optString("message", "Connexion OK");
                    JSONArray out = new JSONArray();
                    try {
                        JSONArray states = new JSONArray(ha.http("GET", url + "/api/states", null, token));
                        for (int i = 0; i < states.length(); i++) {
                            JSONObject st = states.getJSONObject(i);
                            String id = st.optString("entity_id");
                            if (id.startsWith("conversation.")) {
                                JSONObject attrs = st.optJSONObject("attributes");
                                out.put(new JSONObject().put("id", id)
                                        .put("name", attrs != null ? attrs.optString("friendly_name", id) : id));
                            }
                        }
                    } catch (Exception ignored) { }
                    js("onHaTest(true," + q(msg) + "," + q(out.toString()) + ")");
                } catch (Exception e) {
                    js("onHaTest(false," + q(HaClient.explain(e, url)) + ",'')");
                }
            });
        }

        @JavascriptInterface
        public void speak(String text) { Voice.speak(text); }

        @JavascriptInterface
        public void stopSpeak() { Voice.stop(); }

        @JavascriptInterface
        public void startListen(String lang) {
            ui.post(() -> {
                if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED)
                    requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, REQ_MIC);
                else startListenNow();
            });
        }

        @JavascriptInterface
        public void stopListen() {
            ui.post(() -> { if (recognizer != null) recognizer.stopListening(); });
        }
    }

    // ---------------- Dictée ----------------

    @Override
    public void onRequestPermissionsResult(int req, String[] perms, int[] res) {
        super.onRequestPermissionsResult(req, perms, res);
        if (req == REQ_MIC) {
            if (res.length > 0 && res[0] == PackageManager.PERMISSION_GRANTED) startListenNow();
            else js("onSpeech('error'," + q("Micro refusé : autorise-le dans les paramètres de l'appli.") + ")");
        }
    }

    private void startListenNow() {
        if (!SpeechRecognizer.isRecognitionAvailable(this)) {
            js("onSpeech('error'," + q("Aucun service de dictée sur ce téléphone (appli Google).") + ")");
            return;
        }
        Voice.stop();
        if (recognizer == null) {
            recognizer = SpeechRecognizer.createSpeechRecognizer(this);
            recognizer.setRecognitionListener(new RecognitionListener() {
                @Override public void onReadyForSpeech(Bundle p) { js("onSpeech('start','')"); }
                @Override public void onBeginningOfSpeech() {}
                @Override public void onRmsChanged(float v) { js("onSpeech('rms','" + v + "')"); }
                @Override public void onBufferReceived(byte[] b) {}
                @Override public void onEndOfSpeech() {}
                @Override public void onError(int error) {
                    String m;
                    if (error == SpeechRecognizer.ERROR_NO_MATCH || error == SpeechRecognizer.ERROR_SPEECH_TIMEOUT) m = "Rien entendu.";
                    else if (error == SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS) m = "Permission micro manquante.";
                    else if (error == SpeechRecognizer.ERROR_NETWORK || error == SpeechRecognizer.ERROR_NETWORK_TIMEOUT) m = "Dictée : réseau indisponible.";
                    else if (error == SpeechRecognizer.ERROR_CLIENT) { js("onSpeech('end','')"); return; }
                    else m = "Dictée : erreur " + error;
                    js("onSpeech('error'," + q(m) + ")");
                }
                @Override public void onResults(Bundle r) { js("onSpeech('final'," + q(first(r)) + ")"); }
                @Override public void onPartialResults(Bundle r) {
                    String t = first(r);
                    if (!t.isEmpty()) js("onSpeech('partial'," + q(t) + ")");
                }
                @Override public void onEvent(int type, Bundle p) {}
            });
        }
        Intent i = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH);
        i.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM);
        i.putExtra(RecognizerIntent.EXTRA_LANGUAGE, "fr-FR");
        i.putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true);
        i.putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1);
        recognizer.startListening(i);
    }

    private static String first(Bundle r) {
        ArrayList<String> l = r.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
        return (l != null && !l.isEmpty() && l.get(0) != null) ? l.get(0).trim() : "";
    }
}
