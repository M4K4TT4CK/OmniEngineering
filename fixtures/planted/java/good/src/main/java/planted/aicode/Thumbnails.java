package planted.aicode;

import java.awt.image.BufferedImage;
import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Path;
import java.util.Iterator;
import java.util.zip.GZIPInputStream;
import javax.imageio.ImageIO;
import javax.imageio.ImageReader;
import javax.imageio.stream.ImageInputStream;

/** Image and archive handling with the output bounded (ARB-083). */
public final class Thumbnails {
    private static final long MAX_PIXELS = 40_000_000L;
    private static final int MAX_INFLATED = 16 << 20;

    private Thumbnails() {}

    public static BufferedImage load(Path file) throws IOException {
        try (ImageInputStream in = ImageIO.createImageInputStream(file.toFile())) {
            Iterator<ImageReader> readers = ImageIO.getImageReaders(in);
            if (!readers.hasNext()) {
                return null;
            }
            ImageReader reader = readers.next();
            try {
                reader.setInput(in);
                if ((long) reader.getWidth(0) * reader.getHeight(0) > MAX_PIXELS) {
                    throw new IOException("image too large");
                }
                return reader.read(0);
            } finally {
                reader.dispose();
            }
        }
    }

    public static byte[] inflate(InputStream upload) throws IOException {
        try (GZIPInputStream in = new GZIPInputStream(upload)) {
            byte[] out = in.readNBytes(MAX_INFLATED + 1);
            if (out.length > MAX_INFLATED) {
                throw new IOException("inflated payload too large");
            }
            return out;
        }
    }
}
