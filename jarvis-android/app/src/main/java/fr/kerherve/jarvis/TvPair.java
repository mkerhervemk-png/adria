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
import java.security.SecureRandom;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;

/**
 * Appairage Adria TV ← téléphone, sans clavier.
 * TV : petit serveur sur le Wi-Fi (port 8765) tant que l'appli est ouverte ; code à 4 chiffres affiché à l'écran.
 * Téléphone : cherche la TV sur le réseau local, envoie la configuration avec le code.
 * Le code change à chaque ouverture et après 5 essais faux.
 */
public final class TvPair {

    public static final int PORT = 8765;
    private static final SecureRandom RND = new SecureRandom();
    private static volatile ServerSocket server;
    private static volatile boolean running = false;
    private static volatile String code = "";
    private static int fails = 0;

    public interface OnPaired { void paired(); }

    // ------------------------------------------------------------------ TV

    private static void newCode() { code = String.format("%04d", RND.nextInt(10000)); fails = 0; }

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

    /** Pour l'écran TV : {"code":"1234","ip":"192.168.1.x"} (code vide hors Adria TV). */
    public static String info() {
        try { return new JSONObject().put("code", running ? code : "").put("ip", localIp()).toString(); }
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
        if (first.startsWith("GET /adria-tv")) {
            out = new JSONObject().put("adria_tv", 1).put("nom", android.os.Build.MODEL).toString();
        } else if (first.startsWith("POST /pair")) {
            JSONObject o = new JSONObject(body);
            synchronized (TvPair.class) {
                if (code.length() == 4 && code.equals(o.optString("code"))) {
                    JSONObject cfg = o.getJSONObject("cfg");
                    HaClient.prefs(c).edit().putString("cfg_json", cfg.toString()).apply();
                    newCode();
                    out = "{\"ok\":true}";
                    if (cb != null) cb.paired();
                } else {
                    if (++fails >= 5) newCode();
                    out = "{\"ok\":false,\"err\":\"code\"}";
                }
            }
        } else out = "{}";
        byte[] b = out.getBytes(StandardCharsets.UTF_8);
        OutputStream os = s.getOutputStream();
        os.write(("HTTP/1.0 200 OK\r\nContent-Type: application/json\r\nContent-Length: " + b.length
                + "\r\nConnection: close\r\n\r\n").getBytes(StandardCharsets.US_ASCII));
        os.write(b);
        os.flush();
    }

    // ------------------------------------------------------------- téléphone

    /** Cherche Adria TV sur le Wi-Fi et lui envoie la config. Renvoie un message en français. */
    public static String sendFromPhone(Context c, String code) {
        try {
            String cfg = HaClient.prefs(c).getString("cfg_json", "{}");
            if (!new JSONObject(cfg).optString("token").startsWith("ey")) return "Enregistre d'abord les réglages sur ce téléphone.";
            String ip = localIp();
            if (ip.isEmpty()) return "Connecte le téléphone au Wi-Fi de la maison.";
            String pre = ip.substring(0, ip.lastIndexOf('.') + 1);
            ExecutorService ex = Executors.newFixedThreadPool(48);
            List<Future<String>> fs = new ArrayList<>();
            for (int i = 1; i < 255; i++) {
                final String h = pre + i;
                if (h.equals(ip)) continue;
                fs.add(ex.submit(() -> {
                    try (Socket s = new Socket()) { s.connect(new InetSocketAddress(h, PORT), 400); }
                    String r = http("GET", h, "/adria-tv", null);
                    return r.contains("adria_tv") ? h : null;
                }));
            }
            List<String> tvs = new ArrayList<>();
            for (Future<String> f : fs) try { String h = f.get(); if (h != null) tvs.add(h); } catch (Exception ignored) { }
            ex.shutdownNow();
            ex.awaitTermination(1, TimeUnit.SECONDS);
            if (tvs.isEmpty()) return "Aucune Adria TV trouvée : ouvre Adria TV sur la Fire TV (même Wi-Fi) et réessaie.";
            JSONObject body = new JSONObject().put("code", code.trim()).put("cfg", new JSONObject(cfg));
            for (String h : tvs) {
                String r = http("POST", h, "/pair", body.toString());
                if (r.contains("\"ok\":true")) return "✓ Configuration envoyée à la TV (" + h + ").";
            }
            return "Code refusé : vérifie le code affiché sur la TV.";
        } catch (Exception e) {
            return "Échec de l'envoi : " + e.getMessage();
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
