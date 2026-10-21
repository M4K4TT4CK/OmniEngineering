package planted.cwe25;

import java.io.IOException;
import java.nio.file.Path;
import java.util.Map;
import java.util.UUID;

import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.server.ResponseStatusException;

/** CWE-434: a type allowlist, a size cap and a server-chosen name outside the web root. */
@RestController
public class Cwe434Upload {
    private static final Path UPLOAD_DIR = Path.of("/var/app/uploads");
    private static final Map<String, String> ALLOWED = Map.of("image/png", ".png", "image/jpeg", ".jpg");

    @PostMapping("/avatar")
    public String upload(@RequestParam("file") MultipartFile file) throws IOException {
        String ext = ALLOWED.get(file.getContentType());
        if (ext == null || file.getSize() > 2_000_000) {
            throw new ResponseStatusException(HttpStatus.UNSUPPORTED_MEDIA_TYPE);
        }
        String name = UUID.randomUUID() + ext;
        file.transferTo(UPLOAD_DIR.resolve(name));
        return name;
    }
}
