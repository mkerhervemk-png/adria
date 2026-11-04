package fr.kerherve.jarvis;

import android.content.Context;
import android.media.AudioAttributes;
import android.speech.tts.TextToSpeech;
import android.speech.tts.UtteranceProgressListener;

import java.util.Locale;

/** Synthèse vocale partagée (téléphone + voiture). */
public final class Voice {

    public interface Listener { void onSpeech(String type, int start, int end); }

    private static TextToSpeech tts;
    private static boolean ready = false;
    private static String pending = null;
    private static volatile Listener listener;

    public static synchronized void init(Context c) {
        if (tts != null) return;
        tts = new TextToSpeech(c.getApplicationContext(), st -> {
            if (st == TextToSpeech.SUCCESS) {
                tts.setLanguage(Locale.FRANCE);
                tts.setAudioAttributes(new AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_ASSISTANT)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build());
                tts.setOnUtteranceProgressListener(new UtteranceProgressListener() {
                    @Override public void onStart(String id) { fire("start", 0, 0); }
                    @Override public void onDone(String id) { fire("done", 0, 0); }
                    @Override public void onError(String id) { fire("done", 0, 0); }
                    @Override public void onStop(String id, boolean interrupted) { fire("done", 0, 0); }
                    @Override public void onRangeStart(String id, int start, int end, int frame) { fire("word", start, end); }
                });
                ready = true;
                if (pending != null) { speak(pending); pending = null; }
            }
        });
    }

    private static void fire(String t, int s, int e) {
        Listener l = listener;
        if (l != null) l.onSpeech(t, s, e);
    }

    public static void setListener(Listener l) { listener = l; }

    public static void speak(String text) {
        String t = text.replaceAll("[*_#`>]", "");
        if (!ready) { pending = t; return; }
        tts.speak(t, TextToSpeech.QUEUE_FLUSH, null, "jarvis");
    }

    public static void stop() { if (ready) tts.stop(); }
}
