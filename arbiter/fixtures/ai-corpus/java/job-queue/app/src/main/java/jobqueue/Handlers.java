package jobqueue;

import com.fasterxml.jackson.databind.JsonNode;
import jakarta.mail.Authenticator;
import jakarta.mail.Message;
import jakarta.mail.PasswordAuthentication;
import jakarta.mail.Session;
import jakarta.mail.Transport;
import jakarta.mail.internet.InternetAddress;
import jakarta.mail.internet.MimeMessage;

import javax.imageio.ImageIO;
import java.awt.Graphics2D;
import java.awt.RenderingHints;
import java.awt.image.BufferedImage;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Properties;

/** The built-in job types: resize-image and send-email. */
public final class Handlers {
    private Handlers() {}

    public static Map<String, JobQueue.Handler> defaults(Map<String, String> env) {
        Map<String, JobQueue.Handler> m = new LinkedHashMap<>();
        m.put("resize-image", new ResizeImage(env.get("IMAGE_DIR")));
        m.put("send-email", new SendEmail(env));
        return m;
    }

    private static String requireText(JsonNode payload, String field) {
        JsonNode n = payload.get(field);
        if (n == null || !n.isTextual() || n.asText().isBlank()) {
            throw new IllegalArgumentException("payload." + field + " must be a non-empty string");
        }
        return n.asText();
    }

    /**
     * Payload: {"path": "...", "width": 800 (optional)}. Keeps the aspect ratio and writes
     * "name-resized.ext" next to the original. If imageDir is set, paths must be inside it.
     */
    public static final class ResizeImage implements JobQueue.Handler {
        private static final int DEFAULT_WIDTH = 800;
        private final Path baseDir;

        public ResizeImage(String imageDir) {
            this.baseDir = imageDir == null || imageDir.isBlank()
                    ? null : Path.of(imageDir).toAbsolutePath().normalize();
        }

        private Path resolve(JsonNode payload) {
            Path p = Path.of(requireText(payload, "path")).toAbsolutePath().normalize();
            if (baseDir != null && !p.startsWith(baseDir)) {
                throw new IllegalArgumentException("path is outside the allowed image directory");
            }
            return p;
        }

        private static int width(JsonNode payload) {
            JsonNode w = payload.get("width");
            if (w == null) return DEFAULT_WIDTH;
            if (!w.isInt() || w.asInt() < 1 || w.asInt() > 10_000) {
                throw new IllegalArgumentException("payload.width must be an integer between 1 and 10000");
            }
            return w.asInt();
        }

        @Override
        public void validate(JsonNode payload) {
            resolve(payload);
            width(payload);
        }

        @Override
        public Object handle(JsonNode payload) throws Exception {
            Path in = resolve(payload);
            int width = width(payload);

            BufferedImage src = ImageIO.read(in.toFile());
            if (src == null) {
                throw new IllegalArgumentException("Not a readable image: " + in);
            }
            int height = Math.max(1, Math.round((float) src.getHeight() * width / src.getWidth()));
            int type = src.getColorModel().hasAlpha() ? BufferedImage.TYPE_INT_ARGB : BufferedImage.TYPE_INT_RGB;
            BufferedImage out = new BufferedImage(width, height, type);
            Graphics2D g = out.createGraphics();
            try {
                g.setRenderingHint(RenderingHints.KEY_INTERPOLATION, RenderingHints.VALUE_INTERPOLATION_BILINEAR);
                g.setRenderingHint(RenderingHints.KEY_RENDERING, RenderingHints.VALUE_RENDER_QUALITY);
                g.drawImage(src, 0, 0, width, height, null);
            } finally {
                g.dispose();
            }

            String name = in.getFileName().toString();
            int dot = name.lastIndexOf('.');
            String base = dot > 0 ? name.substring(0, dot) : name;
            String ext = dot > 0 ? name.substring(dot + 1) : "png";
            Path target = in.resolveSibling(base + "-resized." + ext);
            if (!ImageIO.write(out, ext, target.toFile())) {
                Files.deleteIfExists(target);
                throw new IllegalStateException("No image writer for format: " + ext);
            }
            return Map.of("outputPath", target.toString(), "width", width, "height", height);
        }
    }

    /**
     * Payload: {"recipient": "...", "subject": "...", "body": "..."}.
     * SMTP env: SMTP_HOST (required), SMTP_PORT (587), SMTP_USER, SMTP_PASSWORD,
     * SMTP_FROM (defaults to SMTP_USER), SMTP_STARTTLS (default true).
     */
    public static final class SendEmail implements JobQueue.Handler {
        private final Map<String, String> env;

        public SendEmail(Map<String, String> env) {
            this.env = env;
        }

        @Override
        public void validate(JsonNode payload) {
            String to = requireText(payload, "recipient");
            String subject = requireText(payload, "subject");
            requireText(payload, "body");
            if (!to.matches("[^\\s@,;<>]+@[^\\s@,;<>]+")) {
                throw new IllegalArgumentException("payload.recipient must be a single email address");
            }
            if (subject.contains("\r") || subject.contains("\n")) {
                throw new IllegalArgumentException("payload.subject must be a single line");
            }
        }

        @Override
        public Object handle(JsonNode payload) throws Exception {
            String host = env.get("SMTP_HOST");
            if (host == null || host.isBlank()) {
                throw new IllegalStateException("SMTP_HOST is not configured");
            }
            String user = env.get("SMTP_USER");
            String password = env.get("SMTP_PASSWORD");
            String from = env.getOrDefault("SMTP_FROM", user);
            if (from == null || from.isBlank()) {
                throw new IllegalStateException("SMTP_FROM (or SMTP_USER) is not configured");
            }
            boolean auth = user != null && !user.isBlank();

            Properties props = new Properties();
            props.put("mail.smtp.host", host);
            props.put("mail.smtp.port", env.getOrDefault("SMTP_PORT", "587"));
            props.put("mail.smtp.auth", String.valueOf(auth));
            props.put("mail.smtp.starttls.enable", env.getOrDefault("SMTP_STARTTLS", "true"));
            props.put("mail.smtp.connectiontimeout", "10000");
            props.put("mail.smtp.timeout", "20000");
            props.put("mail.smtp.writetimeout", "20000");

            Session session = auth
                    ? Session.getInstance(props, new Authenticator() {
                        @Override protected PasswordAuthentication getPasswordAuthentication() {
                            return new PasswordAuthentication(user, password == null ? "" : password);
                        }
                    })
                    : Session.getInstance(props);

            String to = payload.get("recipient").asText();
            MimeMessage msg = new MimeMessage(session);
            msg.setFrom(new InternetAddress(from));
            msg.setRecipient(Message.RecipientType.TO, new InternetAddress(to));
            msg.setSubject(payload.get("subject").asText(), "UTF-8");
            msg.setText(payload.get("body").asText(), "UTF-8");
            Transport.send(msg);
            return Map.of("sentTo", to);
        }
    }
}
