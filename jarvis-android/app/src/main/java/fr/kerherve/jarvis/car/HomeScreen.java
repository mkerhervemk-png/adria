package fr.kerherve.jarvis.car;

import androidx.annotation.NonNull;
import androidx.car.app.CarContext;
import androidx.car.app.CarToast;
import androidx.car.app.Screen;
import androidx.car.app.model.Action;
import androidx.car.app.model.ItemList;
import androidx.car.app.model.ListTemplate;
import androidx.car.app.model.Row;
import androidx.car.app.model.Template;

import fr.kerherve.jarvis.HaClient;
import fr.kerherve.jarvis.Notifs;

/** Écran principal dans la voiture : parler à ADRIA, questions rapides, dernière image de la cour. */
public class HomeScreen extends Screen {

    private static final String[] QUICK = {
            "Qui est à la maison ?",
            "Couleur Tempo demain ?",
            "Batterie de l'Inster ?",
            "Préchauffe l'Inster"};

    public HomeScreen(@NonNull CarContext c) { super(c); }

    @NonNull
    @Override
    public Template onGetTemplate() {
        ItemList.Builder list = new ItemList.Builder();

        list.addItem(new Row.Builder()
                .setTitle("🎙 Parler à ADRIA")
                .addText("Réponds au message d'ADRIA avec le micro")
                .setOnClickListener(() -> {
                    Notifs.adriaSays(getCarContext(), "Je t'écoute. Que veux-tu ?");
                    CarToast.makeText(getCarContext(), "Réponds au message d'ADRIA", CarToast.LENGTH_LONG).show();
                }).build());

        for (String q : QUICK)
            list.addItem(new Row.Builder().setTitle(q)
                    .setOnClickListener(() -> getScreenManager().push(new AnswerScreen(getCarContext(), q)))
                    .build());

        list.addItem(new Row.Builder()
                .setTitle("📷 Caméra de la cour")
                .addText("Image en direct")
                .setOnClickListener(() -> getScreenManager().push(
                        new AlertScreen(getCarContext(), "Cour", "Image en direct", "camera.cameras_de_la_cour", null)))
                .build());

        String sub = HaClient.from(getCarContext()).configured() ? "" : " — configure l'appli sur le téléphone";
        return new ListTemplate.Builder()
                .setTitle("Jarvis · ADRIA" + sub)
                .setHeaderAction(Action.APP_ICON)
                .setSingleList(list.build())
                .build();
    }
}
