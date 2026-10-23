package planted.cwe25;

import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Paths;

import javax.servlet.ServletException;
import javax.servlet.http.HttpServlet;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;
import javax.servlet.http.Part;

import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

/** CWE-434: any upload is stored in the web root under the client's own filename. */
@RestController
public class Cwe434Upload {
    private static final String UPLOAD_DIR = "src/main/resources/static/uploads";

    @PostMapping("/avatar")
    public String upload(@RequestParam("file") MultipartFile file) throws IOException {
        file.transferTo(new File(UPLOAD_DIR, file.getOriginalFilename())); // expect: arbiter/java.upload-client-filename expect-ext: semgrep/semgrep-rules.java.spring.security.injection.tainted-file-path
        return "/uploads/" + file.getOriginalFilename();
    }

    /** The servlet form: `shell.jsp` lands where the container will run it. */
    public static class Attachments extends HttpServlet {
        @Override
        protected void doPost(HttpServletRequest request, HttpServletResponse response) throws IOException, ServletException {
            Part part = request.getPart("attachment");
            String name = part.getSubmittedFileName();
            try (InputStream in = part.getInputStream()) {
                Files.copy(in, Paths.get(getServletContext().getRealPath("/files"), name)); // expect: arbiter/java.upload-client-filename
            }
        }
    }
}
