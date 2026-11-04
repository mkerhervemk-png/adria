package fr.kerherve.jarvis;

import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.graphics.Bitmap;

import androidx.core.app.NotificationCompat;
import androidx.core.app.NotificationManagerCompat;
import androidx.core.app.Person;
import androidx.core.app.RemoteInput;
import androidx.core.graphics.drawable.IconCompat;

import java.util.ArrayList;
import java.util.List;

/**
 * Conversation « ADRIA » au format messagerie : Android Auto la lit à voix haute
 * et propose de répondre à la voix (bouton micro de la voiture).
 */
public final class Notifs {

    public static final String CHANNEL = "adria_conv";
    public static final String CHANNEL_ALERT = "adria_alertes";
    public static final String KEY_REPLY = "reply";
    public static final String ACTION_REPLY = "fr.kerherve.jarvis.REPLY";
    public static final String ACTION_READ = "fr.kerherve.jarvis.READ";
    private static final int ID_CONV = 1;
    private static final int ID_ALERT = 2;

    private static final class Msg {
        final String text; final long time; final boolean me;
        Msg(String t, boolean me) { this.text = t; this.time = System.currentTimeMillis(); this.me = me; }
    }
    private static final List<Msg> history = new ArrayList<>();

    private static Person adria(Context c) {
        return new Person.Builder().setName("ADRIA").setKey("adria").setBot(true)
                .setIcon(IconCompat.createWithResource(c, R.drawable.ic_jarvis)).build();
    }

    private static Person me(Context c) {
        return new Person.Builder().setName(HaClient.from(c).who).setKey("moi").build();
    }

    public static void channels(Context c) {
        NotificationManager nm = c.getSystemService(NotificationManager.class);
        NotificationChannel ch = new NotificationChannel(CHANNEL, "Conversation ADRIA", NotificationManager.IMPORTANCE_HIGH);
        ch.setDescription("Réponses d'ADRIA, lisibles dans Android Auto");
        nm.createNotificationChannel(ch);
        NotificationChannel al = new NotificationChannel(CHANNEL_ALERT, "Alertes caméra", NotificationManager.IMPORTANCE_HIGH);
        al.setDescription("Événements caméra de la cour (voiture)");
        nm.createNotificationChannel(al);
    }

    /** Message d'ADRIA dans la conversation (avec action Répondre à la voix). */
    public static synchronized void adriaSays(Context c, String text) {
        history.add(new Msg(text, false));
        while (history.size() > 8) history.remove(0);
        post(c, CHANNEL, ID_CONV);
    }

    /** Message envoyé par l'utilisateur (affiché dans le fil, sans relecture). */
    public static synchronized void userSaid(Context c, String text) {
        history.add(new Msg(text, true));
        while (history.size() > 8) history.remove(0);
        post(c, CHANNEL, ID_CONV);
    }

    /** Alerte caméra : lue par Android Auto, image sur le téléphone. */
    public static synchronized void alert(Context c, String text, Bitmap img) {
        NotificationCompat.MessagingStyle st = new NotificationCompat.MessagingStyle(me(c));
        st.addMessage(new NotificationCompat.MessagingStyle.Message(text, System.currentTimeMillis(), adria(c)));
        NotificationCompat.Builder b = base(c, CHANNEL_ALERT).setStyle(st);
        if (img != null) b.setLargeIcon(img);
        notify(c, ID_ALERT, b);
    }

    public static void clear(Context c) {
        synchronized (Notifs.class) { history.clear(); }
        NotificationManagerCompat.from(c).cancel(ID_CONV);
    }

    private static void post(Context c, String channel, int id) {
        NotificationCompat.MessagingStyle st = new NotificationCompat.MessagingStyle(me(c));
        Person a = adria(c);
        for (Msg m : history)
            st.addMessage(new NotificationCompat.MessagingStyle.Message(m.text, m.time, m.me ? null : a));
        notify(c, id, base(c, channel).setStyle(st));
    }

    private static NotificationCompat.Builder base(Context c, String channel) {
        RemoteInput ri = new RemoteInput.Builder(KEY_REPLY).setLabel("Répondre à ADRIA").build();
        PendingIntent replyPi = PendingIntent.getBroadcast(c, 10,
                new Intent(c, ReplyReceiver.class).setAction(ACTION_REPLY),
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_MUTABLE);
        PendingIntent readPi = PendingIntent.getBroadcast(c, 11,
                new Intent(c, ReplyReceiver.class).setAction(ACTION_READ),
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        PendingIntent openPi = PendingIntent.getActivity(c, 12,
                new Intent(c, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);

        NotificationCompat.Action reply = new NotificationCompat.Action.Builder(
                IconCompat.createWithResource(c, R.drawable.ic_jarvis), "Répondre", replyPi)
                .addRemoteInput(ri)
                .setAllowGeneratedReplies(true)
                .setSemanticAction(NotificationCompat.Action.SEMANTIC_ACTION_REPLY)
                .setShowsUserInterface(false).build();
        NotificationCompat.Action read = new NotificationCompat.Action.Builder(
                IconCompat.createWithResource(c, R.drawable.ic_jarvis), "Lu", readPi)
                .setSemanticAction(NotificationCompat.Action.SEMANTIC_ACTION_MARK_AS_READ)
                .setShowsUserInterface(false).build();

        return new NotificationCompat.Builder(c, channel)
                .setSmallIcon(R.drawable.ic_stat_jarvis)
                .setCategory(NotificationCompat.CATEGORY_MESSAGE)
                .setPriority(NotificationCompat.PRIORITY_HIGH)
                .setContentIntent(openPi)
                .setAutoCancel(false)
                .addAction(reply)
                .addInvisibleAction(read);
    }

    private static void notify(Context c, int id, NotificationCompat.Builder b) {
        try { NotificationManagerCompat.from(c).notify(id, b.build()); }
        catch (SecurityException ignored) { /* permission notifications refusée */ }
    }
}
