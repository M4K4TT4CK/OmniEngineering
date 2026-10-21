package planted.cwe25;

import java.io.File;
import java.io.IOException;

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
        file.transferTo(new File(UPLOAD_DIR, file.getOriginalFilename())); // expect-ext: semgrep/semgrep-rules.java.spring.security.injection.tainted-file-path
        return "/uploads/" + file.getOriginalFilename();
    }
}
