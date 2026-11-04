package fr.kerherve.jarvis.car;

import android.content.Intent;
import android.graphics.Bitmap;
import android.os.Handler;
import android.os.Looper;

import androidx.annotation.NonNull;
import androidx.car.app.CarAppService;
import androidx.car.app.Screen;
import androidx.car.app.ScreenManager;
import androidx.car.app.Session;
import androidx.car.app.validation.HostValidator;
import androidx.lifecycle.DefaultLifecycleObserver;
import androidx.lifecycle.LifecycleOwner;

import fr.kerherve.jarvis.Notifs;
import fr.kerherve.jarvis.Voice;

/** Point d'entrée Android Auto (catégorie objets connectés). */
public class JarvisCarService extends CarAppService {

    @NonNull
    @Override
    public HostValidator createHostValidator() {
        // Appli installée hors Play Store : on accepte l'hôte Android Auto du téléphone.
        return HostValidator.ALLOW_ALL_HOSTS_VALIDATOR;
    }

    @NonNull
    @Override
    public Session onCreateSession() {
        return new Session() {
            CourWatcher watcher;

            @NonNull
            @Override
            public Screen onCreateScreen(@NonNull Intent intent) {
                Voice.init(getCarContext());
                Notifs.channels(getCarContext());
                Handler ui = new Handler(Looper.getMainLooper());
                watcher = new CourWatcher(getCarContext(), (titre, detail, camera, img) -> ui.post(() -> {
                    ScreenManager sm = getCarContext().getCarService(ScreenManager.class);
                    sm.popToRoot();
                    sm.push(new AlertScreen(getCarContext(), titre, detail, camera, img));
                    Voice.speak("Alerte " + titre + ". " + detail);
                }));
                watcher.start();
                getLifecycle().addObserver(new DefaultLifecycleObserver() {
                    @Override public void onDestroy(@NonNull LifecycleOwner owner) { watcher.stop(); }
                });
                return new HomeScreen(getCarContext());
            }
        };
    }
}
