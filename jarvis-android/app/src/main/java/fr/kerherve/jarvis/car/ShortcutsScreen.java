package fr.kerherve.jarvis.car;

import android.os.Handler;
import android.os.Looper;

import androidx.annotation.NonNull;
import androidx.car.app.CarContext;
import androidx.car.app.CarToast;
import androidx.car.app.Screen;
import androidx.car.app.model.Action;
import androidx.car.app.model.ItemList;
import androidx.car.app.model.ListTemplate;
import androidx.car.app.model.MessageTemplate;
import androidx.car.app.model.Row;
import androidx.car.app.model.Template;

import org.json.JSONObject;

import fr.kerherve.jarvis.HaClient;
import fr.kerherve.jarvis.Voice;

/** Raccourcis dans la voiture : actions directes (0 IA, avec confirmation pour la sécurité) ou questions à ADRIA. */
public class ShortcutsScreen extends Screen {

    // {titre, commande ADRIA (null = question), confirmation, phrase pour ADRIA}
    private static final String[][] ITEMS = {
            {"👁️ Lancer la surveillance", "surveillance seule", "Lancer la surveillance sans alarme sonore ?", null},
            {"🛡️ Lancer l'alarme", "surveillance alarme", "Lancer la surveillance avec l'alarme sonore ?", null},
            {"🛑 Arrête l'alarme", "alarme stop", "Arrêter l'alarme ? La surveillance reste armée.", null},
            {"🚗 Je pars", null, null, "Je pars"},
            {"🏠 Je rentre", null, null, "Je rentre"},
            {"🔥 Préchauffe l'Inster", "clim on", "Préchauffer l'Inster à 22 °C ?", null}};

    public ShortcutsScreen(@NonNull CarContext c) { super(c); }

    @NonNull
    @Override
    public Template onGetTemplate() {
        ItemList.Builder list = new ItemList.Builder();
        for (String[] it : ITEMS) {
            list.addItem(new Row.Builder().setTitle(it[0]).setOnClickListener(() -> {
                if (it[1] == null) getScreenManager().push(new AnswerScreen(getCarContext(), it[3]));
                else getScreenManager().push(new ConfirmScreen(getCarContext(), it[0], it[1], it[2]));
            }).build());
        }
        return new ListTemplate.Builder().setTitle("Raccourcis").setHeaderAction(Action.BACK)
                .setSingleList(list.build()).build();
    }

    /** Confirmation puis exécution directe via script.adria_action_voix. */
    static class ConfirmScreen extends Screen {
        private final String titre, commande, question;
        private String resultat = null;
        private boolean enCours = false;
        private final Handler ui = new Handler(Looper.getMainLooper());

        ConfirmScreen(CarContext c, String titre, String commande, String question) {
            super(c); this.titre = titre; this.commande = commande; this.question = question;
        }

        private void executer() {
            enCours = true; invalidate();
            new Thread(() -> {
                HaClient ha = HaClient.from(getCarContext());
                String r;
                try {
                    JSONObject j = ha.action(commande);
                    String raison = j.optString("raison");
                    r = (j.optBoolean("ok") ? "C'est fait : " : "Non effectué : ") + raison.replace("armee", "armée").replace("arretee", "arrêtée");
                } catch (Exception e) { r = HaClient.explain(e, ha.url); }
                final String fr = r;
                ui.post(() -> { enCours = false; resultat = fr; Voice.speak(fr);
                    CarToast.makeText(getCarContext(), fr, CarToast.LENGTH_LONG).show(); invalidate(); });
            }).start();
        }

        @NonNull
        @Override
        public Template onGetTemplate() {
            if (enCours)
                return new MessageTemplate.Builder("Envoi à la maison…").setTitle(titre).setHeaderAction(Action.BACK).setLoading(true).build();
            if (resultat != null)
                return new MessageTemplate.Builder(resultat).setTitle(titre).setHeaderAction(Action.BACK)
                        .addAction(new Action.Builder().setTitle("OK").setOnClickListener(() -> getScreenManager().pop()).build()).build();
            return new MessageTemplate.Builder(question).setTitle(titre).setHeaderAction(Action.BACK)
                    .addAction(new Action.Builder().setTitle("Annuler").setOnClickListener(() -> getScreenManager().pop()).build())
                    .addAction(new Action.Builder().setTitle("Confirmer").setOnClickListener(this::executer).build())
                    .build();
        }
    }
}
