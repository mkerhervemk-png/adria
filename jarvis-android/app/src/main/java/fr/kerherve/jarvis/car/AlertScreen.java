package fr.kerherve.jarvis.car;

import android.graphics.Bitmap;
import android.os.Handler;
import android.os.Looper;

import androidx.annotation.NonNull;
import androidx.car.app.CarContext;
import androidx.car.app.Screen;
import androidx.car.app.model.Action;
import androidx.car.app.model.CarIcon;
import androidx.car.app.model.Pane;
import androidx.car.app.model.PaneTemplate;
import androidx.car.app.model.Row;
import androidx.car.app.model.Template;
import androidx.core.graphics.drawable.IconCompat;

import fr.kerherve.jarvis.HaClient;

/** Image de caméra sur l'écran de la voiture (alerte ou consultation). */
public class AlertScreen extends Screen {

    private final String titre, detail, camera;
    private Bitmap img;
    private boolean loading;
    private String erreur = null;
    private final Handler ui = new Handler(Looper.getMainLooper());

    public AlertScreen(@NonNull CarContext c, String titre, String detail, String camera, Bitmap img) {
        super(c);
        this.titre = titre; this.detail = detail; this.camera = camera; this.img = img;
        if (img == null) refresh();
    }

    private void refresh() {
        loading = true;
        new Thread(() -> {
            HaClient ha = HaClient.from(getCarContext());
            Bitmap b = null; String err = null;
            try { b = ha.cameraImage(camera); } catch (Exception e) { err = HaClient.explain(e, ha.url); }
            final Bitmap fb = b; final String fe = err;
            ui.post(() -> { loading = false; if (fb != null) img = fb; erreur = fe; invalidate(); });
        }).start();
    }

    @NonNull
    @Override
    public Template onGetTemplate() {
        Pane.Builder p = new Pane.Builder();
        if (loading && img == null) {
            p.setLoading(true);
        } else {
            Row.Builder r = new Row.Builder().setTitle(titre).addText(erreur != null ? erreur : detail);
            if (img != null) {
                CarIcon icon = new CarIcon.Builder(IconCompat.createWithBitmap(img)).build();
                if (getCarContext().getCarAppApiLevel() >= 4) p.setImage(icon);
                else r.setImage(icon, Row.IMAGE_TYPE_LARGE);
            }
            p.addRow(r.build());
            p.addAction(new Action.Builder().setTitle("Actualiser")
                    .setOnClickListener(() -> { refresh(); invalidate(); }).build());
            p.addAction(new Action.Builder().setTitle("Fermer")
                    .setOnClickListener(() -> getScreenManager().pop()).build());
        }
        return new PaneTemplate.Builder(p.build())
                .setTitle("📷 " + titre)
                .setHeaderAction(Action.BACK)
                .build();
    }
}
