package fr.kerherve.jarvis.car;

import android.os.Handler;
import android.os.Looper;

import androidx.annotation.NonNull;
import androidx.car.app.CarContext;
import androidx.car.app.CarToast;
import androidx.car.app.Screen;
import androidx.car.app.model.Action;
import androidx.car.app.model.MessageTemplate;
import androidx.car.app.model.Template;

import org.json.JSONObject;

import fr.kerherve.jarvis.HaClient;
import fr.kerherve.jarvis.MsgBridge;
import fr.kerherve.jarvis.Voice;

/** Question rapide → réponse d'ADRIA affichée et lue sur les haut-parleurs de la voiture. */
public class AnswerScreen extends Screen {

    private final String question;
    private String answer = null;

    public AnswerScreen(@NonNull CarContext c, String question) {
        super(c);
        this.question = question;
        Handler ui = new Handler(Looper.getMainLooper());
        new Thread(() -> {
            HaClient ha = HaClient.from(c);
            String r;
            try {
                JSONObject j = ha.ask(question, null);
                r = j.optString("texte");
            } catch (Exception e) {
                r = HaClient.explain(e, ha.url);
            }
            final String fr = r;
            ui.post(() -> { answer = fr; Voice.speak(fr); invalidate(); });
        }).start();
    }

    @NonNull
    @Override
    public Template onGetTemplate() {
        if (answer == null)
            return new MessageTemplate.Builder("ADRIA réfléchit…")
                    .setTitle(question).setHeaderAction(Action.BACK).setLoading(true).build();

        Action follow = new Action.Builder().setTitle("Répondre à la voix")
                .setOnClickListener(() -> {
                    boolean ok = MsgBridge.send(getCarContext(), "say", answer);
                    CarToast.makeText(getCarContext(), ok ? "Réponds au message d'Adria" : "Installe l'appli « Adria Messages »", CarToast.LENGTH_LONG).show();
                }).build();
        Action again = new Action.Builder().setTitle("Relire")
                .setOnClickListener(() -> Voice.speak(answer)).build();

        return new MessageTemplate.Builder(answer.length() > 480 ? answer.substring(0, 477) + "…" : answer)
                .setTitle(question)
                .setHeaderAction(Action.BACK)
                .addAction(again)
                .addAction(follow)
                .build();
    }
}
