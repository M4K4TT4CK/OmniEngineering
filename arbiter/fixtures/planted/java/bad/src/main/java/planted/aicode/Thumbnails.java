package planted.aicode;

import java.awt.image.BufferedImage;
import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Path;
import java.util.zip.GZIPInputStream;
import javax.imageio.ImageIO;

/** Image and archive handling as model-written upload services ship it (ARB-083). */
public final class Thumbnails {
    private Thumbnails() {}

    public static BufferedImage load(Path file) throws IOException {
        return ImageIO.read(file.toFile()); // expect: arbiter/java.image-decode-unbounded
    }

    public static byte[] inflate(InputStream upload) throws IOException {
        try (GZIPInputStream in = new GZIPInputStream(upload)) {
            return in.readAllBytes(); // expect: arbiter/java.decompress-read-unbounded
        }
    }
}
