package fr.kerherve.jarvis;

import android.content.Context;
import android.content.SharedPreferences;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;

import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

/** Accès Home Assistant partagé par le téléphone et Android Auto (appels bloquants : à lancer hors du thread UI). */
public final class HaClient {

    public final String token, agent, who;
    public String url;            // adresse en cours d'utilisation
    private final String local, ext;
    private static volatile boolean useExt = false; // dernière adresse qui a marché

    private HaClient(String local, String ext, String token, String agent, String who) {
        this.local = local; this.ext = ext; this.token = token; this.agent = agent; this.who = who;
        this.url = (useExt && !ext.isEmpty()) ? ext : local;
    }

    private static String norm(String u) {
        u = (u == null ? "" : u).trim().replaceAll("/+$", "");
        if (!u.isEmpty() && !u.matches("(?i)^https?://.*")) u = "http://" + u;
        return u;
    }

    public static SharedPreferences prefs(Context c) {
        return c.getSharedPreferences("jarvis", Context.MODE_PRIVATE);
    }

    /** Lit la configuration enregistrée par l'interface (cfg_json). */
    public static HaClient from(Context c) {
        try {
            JSONObject o = new JSONObject(prefs(c).getString("cfg_json", "{}"));
            return new HaClient(norm(o.optString("url", "http://192.168.1.53:8123")), norm(o.optString("urlExt", "")),
                    o.optString("token", ""), o.optString("agent", "adria"), o.optString("who", "Mael"));
        } catch (Exception e) {
            return new HaClient("http://192.168.1.53:8123", "", "", "adria", "Mael");
        }
    }

    public boolean configured() { return !token.isEmpty(); }

    // ---------- Requêtes ----------

    /** Pose une question et renvoie {texte, emotion, conversation_id}. */
    public JSONObject ask(String text, String convId) throws Exception {
        JSONObject out = new JSONObject();
        if ("adria".equals(agent)) {
            JSONObject d = new JSONObject();
            d.put("question", text);
            d.put("canal", "assist");
            d.put("locuteur", who);
            if (who.equals("Ryan") || who.equals("Iwan") || who.equals("Aelia")) d.put("enfant", true);
            JSONObject r = new JSONObject(http("POST", "/api/services/script/avatar_demander?return_response", d.toString()));
            JSONObject sr = r.optJSONObject("service_response");
            String t = sr != null ? sr.optString("texte", "") : "";
            out.put("texte", t.isEmpty() ? "ADRIA n'a rien répondu." : t);
            try { out.put("emotion", state("input_select.adria_emotion")); } catch (Exception ignored) { }
        } else {
            JSONObject req = new JSONObject();
            req.put("text", text);
            req.put("language", "fr");
            if (!agent.isEmpty()) req.put("agent_id", agent);
            if (convId != null && !convId.isEmpty()) req.put("conversation_id", convId);
            JSONObject j = new JSONObject(http("POST", "/api/conversation/process", req.toString()));
            out.put("conversation_id", j.optString("conversation_id", ""));
            String t = "";
            JSONObject resp = j.optJSONObject("response");
            if (resp != null) {
                JSONObject sp = resp.optJSONObject("speech");
                JSONObject pl = sp != null ? sp.optJSONObject("plain") : null;
                if (pl != null) t = pl.optString("speech", "");
                if (t.isEmpty() && "error".equals(resp.optString("response_type"))) {
                    JSONObject data = resp.optJSONObject("data");
                    t = "Erreur : " + (data != null ? data.optString("code") : "inconnue");
                }
            }
            out.put("texte", t.isEmpty() ? "Aucune réponse." : t);
        }
        return out;
    }

    /** Action déterministe d'ADRIA (0 IA) : script.adria_action_voix, avec les droits de la personne. */
    public JSONObject action(String commande) throws Exception {
        JSONObject d = new JSONObject().put("commande", commande).put("perso", who.toLowerCase());
        JSONObject r = new JSONObject(http("POST", "/api/services/script/adria_action_voix?return_response", d.toString()));
        JSONObject sr = r.optJSONObject("service_response");
        return sr != null ? sr : new JSONObject().put("ok", false).put("raison", "pas de réponse");
    }

    /** Lampe de la liste blanche d'ADRIA (script.adria_lumiere), ex : "on light.salon". */
    public JSONObject lumiere(String commande) throws Exception {
        JSONObject d = new JSONObject().put("commande", commande);
        JSONObject r = new JSONObject(http("POST", "/api/services/script/adria_lumiere?return_response", d.toString()));
        JSONObject sr = r.optJSONObject("service_response");
        return sr != null ? sr : new JSONObject().put("ok", false).put("raison", "pas de réponse");
    }

    /** États de plusieurs entités : {entity_id: {state, attributes}}. */
    public JSONObject states(org.json.JSONArray ids) {
        JSONObject out = new JSONObject();
        for (int i = 0; i < ids.length(); i++) {
            String id = ids.optString(i);
            try {
                JSONObject st = new JSONObject(http("GET", "/api/states/" + id, null));
                JSONObject o = new JSONObject().put("s", st.optString("state"));
                JSONObject at = st.optJSONObject("attributes");
                if (at != null) o.put("a", at);
                out.put(id, o);
            } catch (Exception e) {
                try { out.put(id, new JSONObject().put("s", "unavailable")); } catch (Exception ignored) { }
            }
        }
        return out;
    }

    /** Événements d'agenda entre deux dates ISO. */
    public String calendar(String entityId, String start, String end) throws Exception {
        return http("GET", "/api/calendars/" + entityId + "?start=" + java.net.URLEncoder.encode(start, "UTF-8")
                + "&end=" + java.net.URLEncoder.encode(end, "UTF-8"), null);
    }

    /** Image caméra en data URL JPEG (pour la WebView). */
    public String cameraDataUrl(String entityId) throws Exception {
        Bitmap b = cameraImage(entityId);
        java.io.ByteArrayOutputStream bo = new java.io.ByteArrayOutputStream();
        b.compress(Bitmap.CompressFormat.JPEG, 70, bo);
        return "data:image/jpeg;base64," + android.util.Base64.encodeToString(bo.toByteArray(), android.util.Base64.NO_WRAP);
    }

    public String state(String entityId) throws Exception {
        return new JSONObject(http("GET", "/api/states/" + entityId, null)).optString("state", "");
    }

    public Bitmap cameraImage(String entityId) throws Exception {
        HttpURLConnection c = connect("GET", "/api/camera_proxy/" + entityId, null);
        int code = c.getResponseCode();
        if (code >= 400) { c.disconnect(); throw new Exception("Image caméra : HTTP " + code); }
        ByteArrayOutputStream bo = new ByteArrayOutputStream();
        try (InputStream is = c.getInputStream()) {
            byte[] buf = new byte[16384]; int n;
            while ((n = is.read(buf)) > 0) bo.write(buf, 0, n);
        }
        c.disconnect();
        BitmapFactory.Options o = new BitmapFactory.Options();
        o.inPreferredConfig = Bitmap.Config.RGB_565;
        Bitmap full = BitmapFactory.decodeByteArray(bo.toByteArray(), 0, bo.size(), o);
        if (full == null) throw new Exception("Image caméra illisible");
        // Android Auto transmet l'image par IPC (limite ~1 Mo) : on la réduit à 600 px de large.
        int w = Math.min(600, full.getWidth());
        int h = Math.round(full.getHeight() * (w / (float) full.getWidth()));
        return w == full.getWidth() ? full : Bitmap.createScaledBitmap(full, w, h, true);
    }

    // ---------- HTTP ----------

    private HttpURLConnection open(String method, String target, int connectMs) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(target).openConnection();
        c.setRequestMethod(method);
        c.setConnectTimeout(connectMs);
        c.setReadTimeout(120000); // ADRIA peut mettre jusqu'à ~60 s
        c.setRequestProperty("Authorization", "Bearer " + token);
        c.setRequestProperty("Content-Type", "application/json");
        return c;
    }

    private static boolean unreachable(Exception e) {
        return e instanceof java.net.ConnectException || e instanceof java.net.UnknownHostException
                || e instanceof java.net.NoRouteToHostException
                || (e instanceof java.net.SocketTimeoutException && String.valueOf(e.getMessage()).contains("connect"));
    }

    /** Ouvre la connexion : adresse locale d'abord, adresse hors maison si la locale ne répond pas (et inversement). */
    private HttpURLConnection connect(String method, String path, String body) throws Exception {
        String first = url, second = url.equals(local) ? ext : local;
        try {
            return send(method, first + path, body, second.isEmpty() ? 8000 : 3000);
        } catch (Exception e) {
            if (second.isEmpty() || !unreachable(e)) throw e;
            HttpURLConnection c = send(method, second + path, body, 8000);
            url = second; useExt = second.equals(ext);
            return c;
        }
    }

    private HttpURLConnection send(String method, String target, String body, int connectMs) throws Exception {
        HttpURLConnection c = open(method, target, connectMs);
        if (body != null) {
            c.setDoOutput(true);
            try (OutputStream os = c.getOutputStream()) { os.write(body.getBytes(StandardCharsets.UTF_8)); }
        }
        c.getResponseCode(); // force la connexion ici
        return c;
    }

    /** Test de connexion avec un jeton pas encore enregistré. */
    public String http(String method, String fullUrl, String body, String tok) throws Exception {
        return new HaClient(local, ext, tok, agent, who).http(method, fullUrl, body);
    }

    /** path relatif (/api/...) ou URL complète (test de connexion). */
    public String http(String method, String path, String body) throws Exception {
        HttpURLConnection c = path.startsWith("http") ? send(method, path, body, 8000) : connect(method, path, body);
        int code = c.getResponseCode();
        InputStream is = code >= 400 ? c.getErrorStream() : c.getInputStream();
        StringBuilder sb = new StringBuilder();
        if (is != null) {
            try (BufferedReader br = new BufferedReader(new InputStreamReader(is, StandardCharsets.UTF_8))) {
                String line;
                while ((line = br.readLine()) != null) sb.append(line);
            }
        }
        c.disconnect();
        if (code == 401) throw new Exception("Jeton refusé (401) : vérifie le jeton d'accès longue durée.");
        if (code == 400 && sb.indexOf("agent") >= 0)
            throw new Exception("Agent inconnu dans HA : choisis-le dans les réglages.");
        if (code >= 400) throw new Exception("HTTP " + code + " " + (sb.length() > 200 ? sb.substring(0, 200) : sb));
        return sb.toString();
    }

    public static String explain(Exception e, String url) {
        String m = String.valueOf(e.getMessage());
        if (e instanceof java.net.MalformedURLException) return "Adresse invalide. Exemple : http://192.168.1.53:8123";
        if (e instanceof javax.net.ssl.SSLException) {
            if (url.startsWith("https://") && url.matches("https://(192\\.168|10\\.|172\\.).*"))
                return "Ton HA local répond en http://, pas en https://. Mets http://" + url.substring(8);
            return "Erreur HTTPS : " + m;
        }
        if (e instanceof java.net.UnknownHostException)
            return "Adresse introuvable. Utilise l'IP locale ou l'adresse Nabu Casa.";
        if (e instanceof java.net.SocketTimeoutException) return "Délai dépassé : HA ne répond pas.";
        if (e instanceof java.net.ConnectException)
            return "Connexion impossible. Hors Wi-Fi de la maison, utilise l'adresse Nabu Casa.";
        return m;
    }
}
