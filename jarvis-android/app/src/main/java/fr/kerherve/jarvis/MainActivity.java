package fr.kerherve.jarvis;

import android.Manifest;
import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.speech.RecognitionListener;
import android.speech.RecognizerIntent;
import android.speech.SpeechRecognizer;
import android.speech.tts.TextToSpeech;
import android.webkit.JavascriptInterface;
import android.webkit.WebSettings;
import android.webkit.WebView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Jarvis v1.1 — interface (assets/index.html, style Kerherve V7) dans une WebView.
 * Le natif gère : appels HTTP vers HA (pas de CORS), dictée Android, synthèse vocale, réglages.
 */
public class MainActivity extends Activity {

    private static final int REQ_MIC = 42;

    private WebView web;
    private SharedPreferences prefs;
    private final ExecutorService io = Executors.newCachedThreadPool();
    private final Handler ui = new Handler(Looper.getMainLooper());

    private TextToSpeech tts;
    private boolean ttsReady = false;
    private SpeechRecognizer recognizer;
    private String pendingLang = "fr-FR";

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        prefs = getSharedPreferences("jarvis", MODE_PRIVATE);
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

        tts = new TextToSpeech(this, st -> {
            if (st == TextToSpeech.SUCCESS) {
                tts.setLanguage(Locale.FRANCE);
                ttsReady = true;
            }
        });
    }

    @Override
    public void onBackPressed() {
        js("document.body.classList.contains('open')?document.body.classList.remove('open'):0");
    }

    @Override
    protected void onDestroy() {
        if (recognizer != null) recognizer.destroy();
        if (tts != null) { tts.stop(); tts.shutdown(); }
        io.shutdownNow();
        web.destroy();
        super.onDestroy();
    }

    // ---------------- Pont JS ----------------

    private void js(String code) {
        ui.post(() -> web.evaluateJavascript(code, null));
    }

    private static String q(String s) { return JSONObject.quote(s == null ? "" : s); }

    private class Bridge {

        @JavascriptInterface
        public String getConfig() {
            String j = prefs.getString("cfg_json", null);
            if (j != null) return j;
            // Migration depuis la v1.0 (clés séparées)
            try {
                JSONObject o = new JSONObject();
                o.put("url", prefs.getString("url", "http://192.168.1.53:8123"));
                o.put("token", prefs.getString("token", ""));
                o.put("agent", "adria");
                o.put("tts", prefs.getBoolean("tts", true));
                o.put("auto", prefs.getBoolean("auto", true));
                return o.toString();
            } catch (Exception e) { return "{}"; }
        }

        @JavascriptInterface
        public void saveConfig(String json) {
            prefs.edit().putString("cfg_json", json).apply();
        }

        @JavascriptInterface
        public void ask(String url, String token, String agent, String lang, String convId, String text, String who) {
            io.execute(() -> {
                try {
                    if ("adria".equals(agent)) {
                        // Vrai cerveau ADRIA : script.avatar_demander, canal assist (pas d'Echo, pas de notif)
                        JSONObject d = new JSONObject();
                        d.put("question", text);
                        d.put("canal", "assist");
                        d.put("locuteur", who);
                        if (who.equals("Ryan") || who.equals("Iwan") || who.equals("Aelia")) d.put("enfant", true);
                        JSONObject r = new JSONObject(http("POST",
                                url + "/api/services/script/avatar_demander?return_response", token, d.toString()));
                        JSONObject sr = r.optJSONObject("service_response");
                        String texte = sr != null ? sr.optString("texte", "") : "";
                        if (texte.isEmpty()) texte = "ADRIA n'a rien répondu.";
                        JSONObject out = new JSONObject().put("response",
                                new JSONObject().put("speech", new JSONObject().put("plain",
                                        new JSONObject().put("speech", texte))));
                        js("onHaReply(true," + q(out.toString()) + ")");
                        return;
                    }
                    JSONObject req = new JSONObject();
                    req.put("text", text);
                    req.put("language", lang);
                    if (!agent.isEmpty()) req.put("agent_id", agent);
                    if (!convId.isEmpty()) req.put("conversation_id", convId);
                    String body = http("POST", url + "/api/conversation/process", token, req.toString());
                    js("onHaReply(true," + q(body) + ")");
                } catch (Exception e) {
                    js("onHaReply(false," + q(explain(e, url)) + ")");
                }
            });
        }

        @JavascriptInterface
        public void test(String url, String token) {
            io.execute(() -> {
                try {
                    String r = http("GET", url + "/api/", token, null);
                    String msg = new JSONObject(r).optString("message", "Connexion OK");
                    // Liste des agents de conversation
                    JSONArray out = new JSONArray();
                    try {
                        JSONArray states = new JSONArray(http("GET", url + "/api/states", token, null));
                        for (int i = 0; i < states.length(); i++) {
                            JSONObject st = states.getJSONObject(i);
                            String id = st.optString("entity_id");
                            if (id.startsWith("conversation.")) {
                                JSONObject a = new JSONObject();
                                a.put("id", id);
                                a.put("name", st.optJSONObject("attributes") != null
                                        ? st.getJSONObject("attributes").optString("friendly_name", id) : id);
                                out.put(a);
                            }
                        }
                    } catch (Exception ignored) { }
                    js("onHaTest(true," + q(msg) + "," + q(out.toString()) + ")");
                } catch (Exception e) {
                    js("onHaTest(false," + q(explain(e, url)) + ",'')");
                }
            });
        }

        @JavascriptInterface
        public void speak(String text) {
            if (ttsReady) tts.speak(text, TextToSpeech.QUEUE_FLUSH, null, "jarvis");
        }

        @JavascriptInterface
        public void stopSpeak() {
            if (ttsReady) tts.stop();
        }

        @JavascriptInterface
        public void startListen(String lang) {
            pendingLang = lang;
            ui.post(() -> {
                if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
                    requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, REQ_MIC);
                } else startListenNow();
            });
        }

        @JavascriptInterface
        public void stopListen() {
            ui.post(() -> { if (recognizer != null) recognizer.stopListening(); });
        }
    }

    // ---------------- HTTP ----------------

    private String http(String method, String target, String token, String body) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(target).openConnection();
        c.setRequestMethod(method);
        c.setConnectTimeout(8000);
        c.setReadTimeout(120000); // ADRIA peut mettre jusqu'à ~60 s
        c.setRequestProperty("Authorization", "Bearer " + token);
        c.setRequestProperty("Content-Type", "application/json");
        if (body != null) {
            c.setDoOutput(true);
            try (OutputStream os = c.getOutputStream()) { os.write(body.getBytes(StandardCharsets.UTF_8)); }
        }
        int code = c.getResponseCode();
        InputStream is = code >= 400 ? c.getErrorStream() : c.getInputStream();
        StringBuilder sb = new StringBuilder();
        if (is != null) {
            try (BufferedReader br = new BufferedReader(new InputStreamReader(is, StandardCharsets.UTF_8))) {
                String line;
                while ((line = br.readLine()) != null) sb.append(line);
            }
        }
        c.disconnect();
        if (code == 401) throw new Exception("Jeton refusé (401) : vérifie le jeton d'accès longue durée.");
        if (code == 400 && sb.indexOf("agent") >= 0)
            throw new Exception("Agent inconnu dans HA : choisis-le dans les réglages (bouton Tester pour charger la liste).");
        if (code >= 400) throw new Exception("HTTP " + code + " " + (sb.length() > 200 ? sb.substring(0, 200) : sb));
        return sb.toString();
    }

    private String explain(Exception e, String url) {
        String m = String.valueOf(e.getMessage());
        if (e instanceof java.net.MalformedURLException)
            return "Adresse invalide. Exemple : http://192.168.1.53:8123";
        if (e instanceof javax.net.ssl.SSLException) {
            if (url.startsWith("https://") && url.matches("https://(192\\.168|10\\.|172\\.).*"))
                return "Ton HA local répond en http://, pas en https://. Mets http://" + url.substring(8);
            return "Erreur HTTPS : " + m;
        }
        if (e instanceof java.net.UnknownHostException)
            return "Adresse introuvable. Utilise l'IP locale (http://192.168.1.53:8123) ou l'adresse Nabu Casa.";
        if (e instanceof java.net.SocketTimeoutException) return "Délai dépassé : HA ne répond pas.";
        if (e instanceof java.net.ConnectException)
            return "Connexion impossible. Hors Wi-Fi de la maison, utilise l'adresse Nabu Casa.";
        return m;
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
        if (ttsReady) tts.stop();
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
        i.putExtra(RecognizerIntent.EXTRA_LANGUAGE, pendingLang);
        i.putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true);
        i.putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1);
        recognizer.startListening(i);
    }

    private static String first(Bundle r) {
        ArrayList<String> l = r.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
        return (l != null && !l.isEmpty() && l.get(0) != null) ? l.get(0).trim() : "";
    }
}
