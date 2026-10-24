package planted.cwe25;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

import javax.servlet.ServletException;
import javax.servlet.http.HttpServlet;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;
import javax.servlet.http.Part;

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

    /** The servlet form: the base name only, an extension allowlist, outside the web root. */
    public static class Attachments extends HttpServlet {
        private static final Set<String> ALLOWED_EXTENSIONS = Set.of("pdf", "txt");

        @Override
        protected void doPost(HttpServletRequest request, HttpServletResponse response) throws IOException, ServletException {
            Part part = request.getPart("attachment");
            String name = Paths.get(part.getSubmittedFileName()).getFileName().toString();
            String ext = name.substring(name.lastIndexOf('.') + 1).toLowerCase();
            if (!ALLOWED_EXTENSIONS.contains(ext)) {
                throw new ServletException("file type not allowed");
            }
            try (InputStream in = part.getInputStream()) {
                Files.copy(in, UPLOAD_DIR.resolve(UUID.randomUUID() + "." + ext));
            }
        }
    }
}
