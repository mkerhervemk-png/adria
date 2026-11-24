package fr.kerherve.jarvis;

import android.content.Context;
import android.media.AudioAttributes;
import android.media.MediaPlayer;
import android.os.Handler;
import android.os.Looper;
import android.speech.tts.TextToSpeech;
import android.speech.tts.UtteranceProgressListener;

import org.json.JSONObject;

import java.util.Locale;

/**
 * Synthèse vocale partagée (téléphone, voiture, TV).
 * Par défaut : voix Brigitte de HA Cloud (même voix que les Echo) via /api/tts_get_url, lue en mp3.
 * Repli automatique sur la voix du téléphone si HA ne répond pas en 5 s (ou réglage « Voix Brigitte » coupé).
 */
public final class Voice {

    public interface Listener { void onSpeech(String type, int start, int end); }

    private static final AudioAttributes ATTR = new AudioAttributes.Builder()
            .setUsage(AudioAttributes.USAGE_ASSISTANT)
            .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build();

    private static TextToSpeech tts;
    private static boolean ready = false;
    private static String pending = null;
    private static volatile Listener listener;
    private static Context app;
    private static final Handler ui = new Handler(Looper.getMainLooper());
    private static MediaPlayer mp;
    private static int seq = 0; // annule les lectures dépassées

    public static synchronized void init(Context c) {
        if (app == null) app = c.getApplicationContext();
        if (tts != null) return;
        tts = new TextToSpeech(app, st -> {
            if (st == TextToSpeech.SUCCESS) {
                tts.setLanguage(Locale.FRANCE);
                tts.setAudioAttributes(ATTR);
                tts.setOnUtteranceProgressListener(new UtteranceProgressListener() {
                    @Override public void onStart(String id) { fire("start", 0, 0); }
                    @Override public void onDone(String id) { fire("done", 0, 0); }
                    @Override public void onError(String id) { fire("done", 0, 0); }
                    @Override public void onStop(String id, boolean interrupted) { fire("done", 0, 0); }
                    @Override public void onRangeStart(String id, int start, int end, int frame) { fire("word", start, end); }
                });
                ready = true;
                if (pending != null) { phone(pending); pending = null; }
            }
        });
    }

    private static void fire(String t, int s, int e) {
        Listener l = listener;
        if (l != null) l.onSpeech(t, s, e);
    }

    public static void setListener(Listener l) { listener = l; }

    private static boolean brigitte() {
        if (app == null) return false;
        try {
            JSONObject o = new JSONObject(SecureCfg.get(app));
            return o.optBoolean("brigitte", true) && !o.optString("token", "").isEmpty();
        } catch (Exception e) { return false; }
    }

    public static void speak(String text) {
        String t = text.replaceAll("[*_#`>]", "").trim();
        if (t.isEmpty()) return;
        stop();
        if (!brigitte()) { phone(t); return; }
        final int my = ++seq;
        // Repli voix du téléphone si l'audio Brigitte n'est pas prêt à temps
        ui.postDelayed(() -> { if (my == seq && (mp == null)) { seq++; phone(t); } }, 5000);
        new Thread(() -> {
            try {
                HaClient h = HaClient.from(app);
                JSONObject body = new JSONObject()
                        .put("engine_id", "tts.home_assistant_cloud")
                        .put("message", t.length() > 1500 ? t.substring(0, 1500) : t)
                        .put("language", "fr-FR")
                        .put("options", new JSONObject().put("voice", "BrigitteNeural"));
                JSONObject r = new JSONObject(h.http("POST", "/api/tts_get_url", body.toString()));
                String path = r.optString("path", "");
                String src = !path.isEmpty() ? h.url + path : r.getString("url");
                ui.post(() -> play(my, src, t));
            } catch (Exception e) {
                ui.post(() -> { if (my == seq) { seq++; phone(t); } });
            }
        }).start();
    }

    private static void play(int my, String src, String text) {
        if (my != seq) return;
        try {
            MediaPlayer p = new MediaPlayer();
            mp = p;
            p.setAudioAttributes(ATTR);
            p.setDataSource(src);
            p.setOnPreparedListener(x -> { if (mp == x) { x.start(); fire("start", 0, 0); } });
            p.setOnCompletionListener(x -> { release(x); fire("done", 0, 0); });
            p.setOnErrorListener((x, w, e) -> {
                release(x);
                if (my == seq) { seq++; phone(text); }
                return true;
            });
            p.prepareAsync();
        } catch (Exception e) {
            mp = null;
            seq++; phone(text);
        }
    }

    private static void release(MediaPlayer x) {
        try { x.release(); } catch (Exception ignored) { }
        if (mp == x) mp = null;
    }

    private static void phone(String t) {
        if (!ready) { pending = t; return; }
        tts.speak(t, TextToSpeech.QUEUE_FLUSH, null, "jarvis");
    }

    public static void stop() {
        seq++;
        MediaPlayer p = mp;
        if (p != null) {
            mp = null;
            try { p.stop(); } catch (Exception ignored) { }
            try { p.release(); } catch (Exception ignored) { }
            fire("done", 0, 0);
        }
        if (ready) tts.stop();
    }
}
