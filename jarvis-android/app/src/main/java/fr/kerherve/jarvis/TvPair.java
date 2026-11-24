package fr.kerherve.jarvis;

import android.content.Context;

import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.NetworkInterface;
import java.net.ServerSocket;
import java.net.Socket;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.KeyPair;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;

/**
 * Appairage Adria TV ← téléphone, sans clavier.
 * TV : petit serveur sur le Wi-Fi (port 8765) tant que l'appli est ouverte ; code « ABCD-EFGH-123456 » affiché.
 * Téléphone : cherche la TV sur le réseau local, vérifie sa clé (empreinte du code) et lui envoie la
 * configuration CHIFFRÉE (v1.15, fiche R3 : voir PairCrypto). Le jeton HA ne circule plus jamais en clair.
 * Le code (et les clés) changent à chaque ouverture, après un appairage réussi, après 5 essais faux et
 * au bout de 5 minutes.
 */
public final class TvPair {

    public static final int PORT = 8765;
    static final long VALIDITE_MS = 5 * 60 * 1000L;
    static final int ESSAIS_MAX = 5;
    private static volatile ServerSocket server;
    private static volatile boolean running = false;
    private static KeyPair cles;
    private static String secret = "", code = "";
    private static long expire = 0;
    private static int fails = 0;

    public interface OnPaired { void paired(); }

    // ------------------------------------------------------------------ TV

    private static synchronized void newCode() {
        try {
            cles = PairCrypto.nouvellePaire();
            secret = PairCrypto.secret();
            code = PairCrypto.codeAffiche(cles.getPublic().getEncoded(), secret);
        } catch (Exception e) {
            cles = null; secret = ""; code = "";
        }
        expire = System.currentTimeMillis() + VALIDITE_MS;
        fails = 0;
    }

    private static synchronized void verifierExpiration() {
        if (running && System.currentTimeMillis() > expire) newCode();
    }

    public static synchronized void start(Context ctx, OnPaired cb) {
        if (running) return;
        running = true;
        newCode();
        final Context c = ctx.getApplicationContext();
        new Thread(() -> {
            try (ServerSocket ss = new ServerSocket()) {
                ss.setReuseAddress(true);
                ss.bind(new InetSocketAddress(PORT));
                synchronized (TvPair.class) { if (!running) return; server = ss; }
                while (!ss.isClosed()) {
                    try (Socket s = ss.accept()) {
                        s.setSoTimeout(4000);
                        handle(c, s, cb);
                    } catch (Exception ignored) { }
                }
            } catch (Exception ignored) { }
        }, "adria-tv-pair").start();
    }

    public static synchronized void stop() {
        running = false;
        ServerSocket ss = server;
        server = null;
        if (ss != null) try { ss.close(); } catch (Exception ignored) { }
    }

    /** Pour l'écran TV : {"code":"ABCD-EFGH-123456","ip":"192.168.1.x","reste":s} (code vide hors Adria TV). */
    public static String info() {
        verifierExpiration();
        try {
            synchronized (TvPair.class) {
                return new JSONObject().put("code", running ? code : "").put("ip", localIp())
                        .put("reste", running ? Math.max(0, (expire - System.currentTimeMillis()) / 1000) : 0).toString();
            }
        }
        catch (Exception e) { return "{}"; }
    }

    private static void handle(Context c, Socket s, OnPaired cb) throws Exception {
        InputStream in = s.getInputStream();
        BufferedReader r = new BufferedReader(new InputStreamReader(in, StandardCharsets.UTF_8));
        String first = r.readLine();
        if (first == null) return;
        int len = 0;
        String h;
        while ((h = r.readLine()) != null && !h.isEmpty())
            if (h.toLowerCase().startsWith("content-length:")) len = Integer.parseInt(h.substring(15).trim());
        len = Math.min(len, 16000);
        char[] buf = new char[len];
        int got = 0;
        while (got < len) { int n = r.read(buf, got, len - got); if (n < 0) break; got += n; }
        String body = new String(buf, 0, got);

        String out;
        verifierExpiration();
        if (first.startsWith("GET /adria-tv")) {
            String pub;
            synchronized (TvPair.class) { pub = cles == null ? "" : PairCrypto.b64(cles.getPublic().getEncoded()); }
            out = new JSONObject().put("adria_tv", 2).put("nom", android.os.Build.MODEL).put("pub", pub).toString();
        } else if (first.startsWith("POST /pair")) {
            out = recevoir(c, body, cb);
        } else out = "{}";
        byte[] b = out.getBytes(StandardCharsets.UTF_8);
        OutputStream os = s.getOutputStream();
        os.write(("HTTP/1.0 200 OK\r\nContent-Type: application/json\r\nContent-Length: " + b.length
                + "\r\nConnection: close\r\n\r\n").getBytes(StandardCharsets.US_ASCII));
        os.write(b);
        os.flush();
    }

    /** Déchiffre et vérifie un envoi du téléphone. Toute erreur compte comme un essai faux. */
    static String recevoir(Context c, String body, OnPaired cb) {
        synchronized (TvPair.class) {
            if (cles == null) return "{\"ok\":false,\"err\":\"code\"}";
            String cfg = null;
            try {
                JSONObject o = new JSONObject(body);
                if (!o.has("ct")) return "{\"ok\":false,\"err\":\"version\"}"; // ancien envoi en clair : refusé
                JSONObject m = new JSONObject(PairCrypto.dechiffrer(cles, o.getString("pub"), o.getString("iv"),
                        o.getString("ct")));
                if (PairCrypto.egal(secret, m.optString("secret"))) cfg = m.getJSONObject("cfg").toString();
            } catch (Exception ignored) { }
            if (cfg == null) {
                if (++fails >= ESSAIS_MAX) newCode();
                return "{\"ok\":false,\"err\":\"code\"}";
            }
            SecureCfg.put(c, cfg);
            newCode(); // usage unique
        }
        if (cb != null) cb.paired();
        return "{\"ok\":true}";
    }

    // ------------------------------------------------------------- téléphone

    /** Cherche Adria TV sur le Wi-Fi et lui envoie la config chiffrée. Renvoie un message en français. */
    public static String sendFromPhone(Context c, String tape) {
        try {
            String[] code = PairCrypto.lireCode(tape);
            if (code == null) return "Tape le code complet affiché sur la TV (ex. ABCD-EFGH-123456).";
            String cfg = SecureCfg.get(c);
            if (!new JSONObject(cfg).optString("token").startsWith("ey")) return "Enregistre d'abord les réglages sur ce téléphone.";
            String ip = localIp();
            if (ip.isEmpty()) return "Connecte le téléphone au Wi-Fi de la maison.";
            String pre = ip.substring(0, ip.lastIndexOf('.') + 1);
            ExecutorService ex = Executors.newFixedThreadPool(48);
            List<Future<String[]>> fs = new ArrayList<>();
            for (int i = 1; i < 255; i++) {
                final String h = pre + i;
                if (h.equals(ip)) continue;
                fs.add(ex.submit(() -> {
                    try (Socket s = new Socket()) { s.connect(new InetSocketAddress(h, PORT), 400); }
                    JSONObject r = new JSONObject(http("GET", h, "/adria-tv", null));
                    return r.has("adria_tv") ? new String[]{h, r.optString("pub")} : null;
                }));
            }
            List<String[]> tvs = new ArrayList<>();
            for (Future<String[]> f : fs) try { String[] h = f.get(); if (h != null) tvs.add(h); } catch (Exception ignored) { }
            ex.shutdownNow();
            ex.awaitTermination(1, TimeUnit.SECONDS);
            if (tvs.isEmpty()) return "Aucune Adria TV trouvée : ouvre Adria TV sur la Fire TV (même Wi-Fi) et réessaie.";
            boolean vue = false;
            for (String[] tv : tvs) {
                byte[] pub;
                try { pub = PairCrypto.deB64(tv[1]); } catch (Exception e) { continue; }
                // Vérification de l'identité de la TV : sa clé doit correspondre au code tapé.
                if (pub.length == 0 || !PairCrypto.egal(PairCrypto.empreinte(pub), code[0])) continue;
                vue = true;
                String clair = new JSONObject().put("secret", code[1]).put("cfg", new JSONObject(cfg)).toString();
                String[] env = PairCrypto.chiffrer(pub, clair);
                String body = new JSONObject().put("v", 2).put("pub", env[0]).put("iv", env[1]).put("ct", env[2]).toString();
                String r = http("POST", tv[0], "/pair", body);
                if (r.contains("\"ok\":true")) return "✓ Configuration envoyée chiffrée à la TV (" + tv[0] + ").";
            }
            if (!vue) return "Aucune TV ne correspond à ce code : vérifie le code affiché (il change toutes les 5 minutes). "
                    + "Si Adria TV est à jour et le code juste, ne continue pas : un autre appareil se fait peut-être passer pour la TV.";
            return "Code refusé : vérifie les 6 derniers chiffres affichés sur la TV.";
        } catch (Exception e) {
            return "Échec de l'envoi (" + e.getClass().getSimpleName() + ").";
        }
    }

    private static String http(String m, String host, String path, String body) throws Exception {
        HttpURLConnection k = (HttpURLConnection) new URL("http://" + host + ":" + PORT + path).openConnection();
        k.setConnectTimeout(1500);
        k.setReadTimeout(3000);
        k.setRequestMethod(m);
        if (body != null) {
            k.setDoOutput(true);
            k.setRequestProperty("Content-Type", "application/json");
            k.getOutputStream().write(body.getBytes(StandardCharsets.UTF_8));
        }
        try (InputStream in = k.getInputStream()) {
            byte[] b = new byte[4096];
            int n = in.read(b);
            return n > 0 ? new String(b, 0, n, StandardCharsets.UTF_8) : "";
        } finally { k.disconnect(); }
    }

    static String localIp() {
        try {
            List<NetworkInterface> l = Collections.list(NetworkInterface.getNetworkInterfaces());
            // Wi-Fi / Ethernet d'abord (évite l'adresse 10.x des données mobiles)
            l.sort((x, y) -> Integer.compare(rank(x), rank(y)));
            for (NetworkInterface ni : l) {
                if (!ni.isUp() || ni.isLoopback() || ni.getName().startsWith("rmnet") || ni.getName().startsWith("ccmni")) continue;
                for (InetAddress a : Collections.list(ni.getInetAddresses()))
                    if (a instanceof Inet4Address && a.isSiteLocalAddress()) return a.getHostAddress();
            }
        } catch (Exception ignored) { }
        return "";
    }

    private static int rank(NetworkInterface n) {
        String x = n.getName();
        return x.startsWith("wlan") ? 0 : x.startsWith("eth") ? 1 : 2;
    }
}
