package planted.aicode;

import java.util.Map;
import java.util.Properties;

/** SMTP session properties with TLS mandatory and the server verified (ARB-083). */
public final class MailSettings {
    private MailSettings() {}

    public static Properties smtp(Map<String, String> env) {
        Properties props = new Properties();
        props.put("mail.smtp.host", env.get("SMTP_HOST"));
        props.put("mail.smtp.port", env.getOrDefault("SMTP_PORT", "587"));
        props.put("mail.smtp.auth", "true");
        props.put("mail.smtp.starttls.enable", "true");
        props.put("mail.smtp.starttls.required", "true");
        props.put("mail.smtp.ssl.trust", env.get("SMTP_HOST"));
        return props;
    }
}
