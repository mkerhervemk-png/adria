package fr.kerherve.jarvis;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.speech.RecognitionListener;
import android.speech.RecognizerIntent;
import android.speech.SpeechRecognizer;
import android.speech.tts.TextToSpeech;
import android.text.InputType;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.view.inputmethod.EditorInfo;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

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
 * Jarvis — interface vocale pour Home Assistant (Conversation API).
 * POST {url}/api/conversation/process  {text, language, agent_id?, conversation_id?}
 */
public class MainActivity extends Activity {

    private static final int C_BG = Color.parseColor("#05080F");
    private static final int C_PANEL = Color.parseColor("#0C1322");
    private static final int C_LINE = Color.parseColor("#1B2A44");
    private static final int C_TXT = Color.parseColor("#D8E6FF");
    private static final int C_DIM = Color.parseColor("#7D8FB0");
    private static final int C_ACC = Color.parseColor("#29D3FF");
    private static final int C_OK = Color.parseColor("#3EE08F");
    private static final int C_ERR = Color.parseColor("#FF5C7A");
    private static final int C_ME = Color.parseColor("#13305A");
    private static final int REQ_MIC = 42;

    private SharedPreferences prefs;
    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private final Handler ui = new Handler(Looper.getMainLooper());

    private LinearLayout log;
    private ScrollView scroll;
    private EditText input;
    private Button mic;
    private TextView status;

    private TextToSpeech tts;
    private boolean ttsReady = false;
    private SpeechRecognizer recognizer;
    private boolean listening = false;
    private boolean busy = false;
    private String conversationId = null;

    // ---------------- Cycle de vie ----------------

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        prefs = getSharedPreferences("jarvis", MODE_PRIVATE);
        getWindow().setStatusBarColor(C_BG);
        getWindow().setNavigationBarColor(C_BG);
        setContentView(buildUi());

        tts = new TextToSpeech(this, st -> {
            if (st == TextToSpeech.SUCCESS) {
                tts.setLanguage(locale());
                ttsReady = true;
            }
        });

        refreshStatus();
        if (url().isEmpty() || token().isEmpty()) openSettings();
        else addMsg("Prêt. Appuie sur le micro ou écris une consigne.", false);
    }

    @Override
    protected void onDestroy() {
        if (recognizer != null) recognizer.destroy();
        if (tts != null) { tts.stop(); tts.shutdown(); }
        io.shutdownNow();
        super.onDestroy();
    }

    // ---------------- Interface ----------------

    private int dp(float v) {
        return (int) TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v, getResources().getDisplayMetrics());
    }

    private GradientDrawable box(int fill, int stroke, float radius) {
        GradientDrawable g = new GradientDrawable();
        g.setColor(fill);
        g.setCornerRadius(dp(radius));
        if (stroke != 0) g.setStroke(dp(1), stroke);
        return g;
    }

    private View buildUi() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(C_BG);
        root.setFitsSystemWindows(true);

        // En-tête
        LinearLayout head = new LinearLayout(this);
        head.setGravity(Gravity.CENTER_VERTICAL);
        head.setPadding(dp(16), dp(14), dp(12), dp(14));
        LinearLayout titles = new LinearLayout(this);
        titles.setOrientation(LinearLayout.VERTICAL);
        TextView title = new TextView(this);
        title.setText("JARVIS");
        title.setTextColor(C_TXT);
        title.setTextSize(18);
        title.setLetterSpacing(0.2f);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        status = new TextView(this);
        status.setTextColor(C_DIM);
        status.setTextSize(12);
        titles.addView(title);
        titles.addView(status);
        head.addView(titles, new LinearLayout.LayoutParams(0, -2, 1));
        head.addView(iconBtn("↺", v -> {
            conversationId = null;
            log.removeAllViews();
            addMsg("Nouvelle conversation.", false);
        }));
        head.addView(iconBtn("⚙", v -> openSettings()));
        root.addView(head);
        root.addView(divider());

        // Journal
        scroll = new ScrollView(this);
        log = new LinearLayout(this);
        log.setOrientation(LinearLayout.VERTICAL);
        log.setPadding(dp(14), dp(14), dp(14), dp(14));
        scroll.addView(log);
        root.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1));

        // Bouton micro
        mic = new Button(this);
        mic.setText("🎙");
        mic.setTextSize(32);
        mic.setTextColor(C_ACC);
        setMicStyle(false);
        mic.setOnClickListener(v -> toggleListen());
        LinearLayout.LayoutParams mp = new LinearLayout.LayoutParams(dp(96), dp(96));
        mp.gravity = Gravity.CENTER_HORIZONTAL;
        mp.setMargins(0, dp(8), 0, dp(10));
        root.addView(mic, mp);
        root.addView(divider());

        // Champ texte + envoi
        LinearLayout bar = new LinearLayout(this);
        bar.setPadding(dp(12), dp(10), dp(12), dp(12));
        bar.setGravity(Gravity.CENTER_VERTICAL);
        input = new EditText(this);
        input.setHint("Écris une consigne…");
        input.setHintTextColor(C_DIM);
        input.setTextColor(C_TXT);
        input.setBackground(box(C_PANEL, C_LINE, 12));
        input.setPadding(dp(12), dp(12), dp(12), dp(12));
        input.setSingleLine(true);
        input.setImeOptions(EditorInfo.IME_ACTION_SEND);
        input.setOnEditorActionListener((tv, id, ev) -> {
            if (id == EditorInfo.IME_ACTION_SEND
                    || (ev != null && ev.getKeyCode() == KeyEvent.KEYCODE_ENTER && ev.getAction() == KeyEvent.ACTION_DOWN)) {
                sendFromInput();
                return true;
            }
            return false;
        });
        bar.addView(input, new LinearLayout.LayoutParams(0, -2, 1));
        Button send = new Button(this);
        send.setText("➤");
        send.setTextColor(C_BG);
        send.setTextSize(18);
        send.setBackground(box(C_ACC, 0, 12));
        send.setOnClickListener(v -> sendFromInput());
        LinearLayout.LayoutParams sp = new LinearLayout.LayoutParams(dp(56), dp(48));
        sp.setMargins(dp(8), 0, 0, 0);
        bar.addView(send, sp);
        root.addView(bar);
        return root;
    }

    private View divider() {
        View d = new View(this);
        d.setBackgroundColor(C_LINE);
        d.setLayoutParams(new LinearLayout.LayoutParams(-1, dp(1)));
        return d;
    }

    private Button iconBtn(String label, View.OnClickListener l) {
        Button b = new Button(this);
        b.setText(label);
        b.setTextColor(C_TXT);
        b.setTextSize(16);
        b.setBackground(box(C_BG, C_LINE, 10));
        b.setOnClickListener(l);
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(dp(46), dp(42));
        p.setMargins(dp(6), 0, 0, 0);
        b.setLayoutParams(p);
        return b;
    }

    private void setMicStyle(boolean on) {
        GradientDrawable g = new GradientDrawable();
        g.setShape(GradientDrawable.OVAL);
        g.setColor(on ? Color.parseColor("#0F3A2A") : Color.parseColor("#0F3550"));
        g.setStroke(dp(2), on ? C_OK : C_ACC);
        mic.setBackground(g);
        mic.setTextColor(on ? C_OK : C_ACC);
        mic.setAlpha(busy ? 0.5f : 1f);
    }

    private TextView addMsg(String text, boolean me) {
        TextView t = new TextView(this);
        t.setText(text);
        t.setTextColor(C_TXT);
        t.setTextSize(16);
        t.setTextIsSelectable(true);
        t.setPadding(dp(14), dp(10), dp(14), dp(10));
        t.setBackground(me ? box(C_ME, 0, 14) : box(C_PANEL, C_LINE, 14));
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(-2, -2);
        p.gravity = me ? Gravity.END : Gravity.START;
        p.setMargins(me ? dp(48) : 0, dp(5), me ? 0 : dp(48), dp(5));
        log.addView(t, p);
        scrollDown();
        return t;
    }

    private void addErr(String text) {
        TextView t = new TextView(this);
        t.setText(text);
        t.setTextColor(C_ERR);
        t.setTextSize(14);
        t.setGravity(Gravity.CENTER);
        t.setPadding(dp(8), dp(6), dp(8), dp(6));
        log.addView(t, new LinearLayout.LayoutParams(-1, -2));
        scrollDown();
    }

    private void scrollDown() {
        scroll.post(() -> scroll.fullScroll(View.FOCUS_DOWN));
    }

    // ---------------- Réglages ----------------

    private String url() { return prefs.getString("url", "").trim().replaceAll("/+$", ""); }
    private String token() { return prefs.getString("token", "").trim(); }
    private String agent() { return prefs.getString("agent", "").trim(); }
    private String lang() { String l = prefs.getString("lang", "fr").trim(); return l.isEmpty() ? "fr" : l; }
    private boolean speakOn() { return prefs.getBoolean("tts", true); }
    private boolean autoSend() { return prefs.getBoolean("auto", true); }

    private Locale locale() {
        String l = lang();
        return "fr".equalsIgnoreCase(l) ? Locale.FRANCE : Locale.forLanguageTag(l);
    }

    private void refreshStatus() {
        if (url().isEmpty() || token().isEmpty()) status.setText("non configuré");
        else status.setText(url().replaceFirst("^https?://", "") + (agent().isEmpty() ? "" : " · " + agent()));
    }

    private EditText field(LinearLayout parent, String label, String value, String hint, int type) {
        TextView l = new TextView(this);
        l.setText(label);
        l.setTextColor(C_DIM);
        l.setTextSize(13);
        l.setPadding(0, dp(10), 0, dp(4));
        parent.addView(l);
        EditText e = new EditText(this);
        e.setText(value);
        e.setHint(hint);
        e.setHintTextColor(C_DIM);
        e.setTextColor(C_TXT);
        e.setInputType(type);
        e.setSingleLine(true);
        e.setBackground(box(C_BG, C_LINE, 10));
        e.setPadding(dp(10), dp(10), dp(10), dp(10));
        parent.addView(e);
        return e;
    }

    private CheckBox check(LinearLayout parent, String label, boolean v) {
        CheckBox c = new CheckBox(this);
        c.setText(label);
        c.setTextColor(C_TXT);
        c.setChecked(v);
        c.setButtonTintList(android.content.res.ColorStateList.valueOf(C_ACC));
        parent.addView(c);
        return c;
    }

    private void openSettings() {
        ScrollView sv = new ScrollView(this);
        LinearLayout f = new LinearLayout(this);
        f.setOrientation(LinearLayout.VERTICAL);
        f.setPadding(dp(18), dp(6), dp(18), dp(6));
        f.setBackgroundColor(C_PANEL);
        sv.addView(f);

        EditText eUrl = field(f, "URL Home Assistant", url(), "http://192.168.1.x:8123 ou https://xxxx.ui.nabu.casa",
                InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        EditText eTok = field(f, "Long-Lived Access Token", token(), "eyJhbGciOi…",
                InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        EditText eAg = field(f, "Agent de conversation (agent_id)", agent(), "ex : conversation.adria (vide = défaut)",
                InputType.TYPE_CLASS_TEXT);
        EditText eLang = field(f, "Langue", lang(), "fr", InputType.TYPE_CLASS_TEXT);
        CheckBox cT = check(f, "Lire la réponse à voix haute", speakOn());
        CheckBox cA = check(f, "Envoi automatique après la dictée", autoSend());
        TextView res = new TextView(this);
        res.setTextSize(13);
        res.setPadding(0, dp(8), 0, dp(4));
        f.addView(res);
        TextView note = new TextView(this);
        note.setText("Le jeton reste stocké uniquement sur ce téléphone.");
        note.setTextColor(C_DIM);
        note.setTextSize(12);
        f.addView(note);

        AlertDialog d = new AlertDialog.Builder(this, android.R.style.Theme_Material_Dialog)
                .setTitle("Configuration Home Assistant")
                .setView(sv)
                .setPositiveButton("Enregistrer", null)
                .setNeutralButton("Tester", null)
                .setNegativeButton("Fermer", null)
                .create();
        d.setOnShowListener(x -> {
            d.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v -> {
                prefs.edit()
                        .putString("url", eUrl.getText().toString().trim().replaceAll("/+$", ""))
                        .putString("token", eTok.getText().toString().trim())
                        .putString("agent", eAg.getText().toString().trim())
                        .putString("lang", eLang.getText().toString().trim())
                        .putBoolean("tts", cT.isChecked())
                        .putBoolean("auto", cA.isChecked())
                        .apply();
                if (ttsReady) tts.setLanguage(locale());
                refreshStatus();
                d.dismiss();
            });
            d.getButton(AlertDialog.BUTTON_NEUTRAL).setOnClickListener(v -> {
                res.setTextColor(C_DIM);
                res.setText("Test en cours…");
                String u = eUrl.getText().toString().trim().replaceAll("/+$", "");
                String t = eTok.getText().toString().trim();
                io.execute(() -> {
                    String msg; boolean ok;
                    try {
                        String body = http("GET", u + "/api/", t, null);
                        msg = "✓ " + new JSONObject(body).optString("message", "Connexion OK");
                        ok = true;
                    } catch (Exception e) {
                        msg = "✗ " + explain(e);
                        ok = false;
                    }
                    final String m = msg; final boolean k = ok;
                    ui.post(() -> { res.setTextColor(k ? C_OK : C_ERR); res.setText(m); });
                });
            });
        });
        d.show();
    }

    // ---------------- Envoi à Home Assistant ----------------

    private void sendFromInput() {
        String t = input.getText().toString().trim();
        if (t.isEmpty()) return;
        input.setText("");
        ask(t);
    }

    private void ask(String text) {
        if (url().isEmpty() || token().isEmpty()) { openSettings(); return; }
        if (busy) return;
        busy = true;
        setMicStyle(false);
        addMsg(text, true);
        TextView pending = addMsg("…", false);

        final String u = url(), t = token(), a = agent(), l = lang(), cid = conversationId;
        io.execute(() -> {
            String reply = null, err = null;
            try {
                JSONObject req = new JSONObject();
                req.put("text", text);
                req.put("language", l);
                if (!a.isEmpty()) req.put("agent_id", a);
                if (cid != null) req.put("conversation_id", cid);

                JSONObject j = new JSONObject(http("POST", u + "/api/conversation/process", t, req.toString()));
                String newCid = j.optString("conversation_id", null);
                if (newCid != null && !newCid.isEmpty() && !"null".equals(newCid)) conversationId = newCid;

                JSONObject resp = j.optJSONObject("response");
                if (resp != null) {
                    JSONObject speech = resp.optJSONObject("speech");
                    if (speech != null) {
                        JSONObject plain = speech.optJSONObject("plain");
                        if (plain != null) reply = plain.optString("speech", null);
                    }
                    if ((reply == null || reply.isEmpty()) && "error".equals(resp.optString("response_type"))) {
                        JSONObject data = resp.optJSONObject("data");
                        reply = "Erreur : " + (data != null ? data.optString("code") : "inconnue");
                    }
                }
                if (reply == null || reply.isEmpty()) reply = "Aucune réponse.";
            } catch (Exception e) {
                err = explain(e);
            }
            final String r = reply, e = err;
            ui.post(() -> {
                busy = false;
                setMicStyle(listening);
                if (e != null) {
                    log.removeView(pending);
                    addErr(e);
                } else {
                    pending.setText(r);
                    speak(r);
                }
            });
        });
    }

    private String http(String method, String target, String token, String body) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(target).openConnection();
        c.setRequestMethod(method);
        c.setConnectTimeout(8000);
        c.setReadTimeout(60000); // un agent LLM peut être lent
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
        if (code == 401) throw new Exception("Jeton refusé (401) — vérifie le Long-Lived Token.");
        if (code >= 400) throw new Exception("HTTP " + code + " " + (sb.length() > 200 ? sb.substring(0, 200) : sb));
        return sb.toString();
    }

    private String explain(Exception e) {
        String m = String.valueOf(e.getMessage());
        if (e instanceof java.net.UnknownHostException)
            return "Adresse introuvable. Sur Android, « homeassistant.local » marche mal : mets l'IP locale.";
        if (e instanceof java.net.SocketTimeoutException) return "Délai dépassé : HA ne répond pas.";
        if (e instanceof java.net.ConnectException) return "Connexion impossible : es-tu sur le Wi-Fi de la maison ?";
        if (e instanceof javax.net.ssl.SSLException) return "Erreur HTTPS : " + m;
        return m;
    }

    // ---------------- Voix ----------------

    private void speak(String t) {
        if (!speakOn() || !ttsReady) return;
        tts.speak(t.replaceAll("[*_#`>]", ""), TextToSpeech.QUEUE_FLUSH, null, "jarvis");
    }

    private void toggleListen() {
        if (listening) { if (recognizer != null) recognizer.stopListening(); return; }
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, REQ_MIC);
            return;
        }
        startListen();
    }

    @Override
    public void onRequestPermissionsResult(int req, String[] perms, int[] res) {
        super.onRequestPermissionsResult(req, perms, res);
        if (req == REQ_MIC) {
            if (res.length > 0 && res[0] == PackageManager.PERMISSION_GRANTED) startListen();
            else addErr("Micro refusé. Autorise-le dans les paramètres de l'appli.");
        }
    }

    private void startListen() {
        if (!SpeechRecognizer.isRecognitionAvailable(this)) {
            addErr("Aucun service de dictée sur ce téléphone (installe/active l'appli Google).");
            return;
        }
        if (ttsReady) tts.stop();
        if (recognizer == null) {
            recognizer = SpeechRecognizer.createSpeechRecognizer(this);
            recognizer.setRecognitionListener(new RecognitionListener() {
                @Override public void onReadyForSpeech(Bundle p) { listening = true; setMicStyle(true); }
                @Override public void onBeginningOfSpeech() {}
                @Override public void onRmsChanged(float v) {
                    float s = 1f + Math.min(Math.max(v, 0f), 10f) / 50f;
                    mic.setScaleX(s); mic.setScaleY(s);
                }
                @Override public void onBufferReceived(byte[] b) {}
                @Override public void onEndOfSpeech() {}
                @Override public void onError(int error) {
                    stopUi();
                    if (error == SpeechRecognizer.ERROR_NO_MATCH || error == SpeechRecognizer.ERROR_SPEECH_TIMEOUT)
                        addErr("Rien entendu.");
                    else if (error == SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS)
                        addErr("Permission micro manquante.");
                    else if (error == SpeechRecognizer.ERROR_NETWORK || error == SpeechRecognizer.ERROR_NETWORK_TIMEOUT)
                        addErr("Dictée : réseau indisponible.");
                    else addErr("Dictée : erreur " + error);
                }
                @Override public void onResults(Bundle r) {
                    stopUi();
                    String t = first(r);
                    if (t.isEmpty()) return;
                    if (autoSend()) { input.setText(""); ask(t); }
                    else { input.setText(t); input.setSelection(t.length()); }
                }
                @Override public void onPartialResults(Bundle r) {
                    String t = first(r);
                    if (!t.isEmpty()) input.setText(t);
                }
                @Override public void onEvent(int type, Bundle p) {}
            });
        }
        Intent i = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH);
        i.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM);
        i.putExtra(RecognizerIntent.EXTRA_LANGUAGE, locale().toLanguageTag());
        i.putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true);
        i.putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1);
        input.setText("");
        recognizer.startListening(i);
    }

    private void stopUi() {
        listening = false;
        mic.setScaleX(1f); mic.setScaleY(1f);
        setMicStyle(false);
    }

    private static String first(Bundle r) {
        ArrayList<String> l = r.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
        return (l != null && !l.isEmpty() && l.get(0) != null) ? l.get(0).trim() : "";
    }
}
