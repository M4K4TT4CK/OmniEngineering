package com.example.upload;

import java.awt.RenderingHints;
import java.awt.image.BufferedImage;
import java.io.IOException;
import java.io.InputStream;
import java.io.UncheckedIOException;
import java.nio.file.FileAlreadyExistsException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.List;
import java.util.stream.Stream;
import javax.imageio.ImageIO;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.server.ResponseStatusException;

/** Stores files in a flat directory on local disk. Thumbnails live in a reserved subdirectory. */
@Service
public class FileStorage {

    public record FileInfo(String name, long size, Instant uploadedAt) {}

    static final String THUMBNAIL_DIR = "thumbnails";

    private final Path root;
    private final Path thumbnails;
    private final int thumbnailSize;

    public FileStorage(@Value("${app.storage-dir}") String dir,
                       @Value("${app.thumbnail-size:128}") int thumbnailSize) throws IOException {
        this.root = Path.of(dir).toAbsolutePath().normalize();
        this.thumbnails = root.resolve(THUMBNAIL_DIR);
        this.thumbnailSize = thumbnailSize;
        Files.createDirectories(thumbnails);
    }

    public FileInfo store(MultipartFile upload) {
        String name = validate(upload.getOriginalFilename());
        try (InputStream in = upload.getInputStream()) {
            Files.copy(in, root.resolve(name));
        } catch (FileAlreadyExistsException e) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "File already exists: " + name);
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
        return info(root.resolve(name));
    }

    public List<FileInfo> list() {
        try (Stream<Path> files = Files.list(root)) {
            return files.filter(Files::isRegularFile)
                    .map(this::info)
                    .sorted((a, b) -> a.name().compareTo(b.name()))
                    .toList();
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    public Path load(String name) {
        Path file = root.resolve(validate(name));
        if (!Files.isRegularFile(file)) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "No such file: " + name);
        }
        return file;
    }

    public void delete(String name) {
        Path file = load(name);
        try {
            Files.delete(file);
            Files.deleteIfExists(thumbnailPath(name));
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    /** Generates (or regenerates) a PNG thumbnail and returns its path. */
    public Path createThumbnail(String name) {
        Path file = load(name);
        try {
            BufferedImage src = ImageIO.read(file.toFile());
            if (src == null) {
                throw new ResponseStatusException(HttpStatus.UNSUPPORTED_MEDIA_TYPE, "Not a supported image: " + name);
            }
            double scale = Math.min(1.0, (double) thumbnailSize / Math.max(src.getWidth(), src.getHeight()));
            int w = Math.max(1, (int) Math.round(src.getWidth() * scale));
            int h = Math.max(1, (int) Math.round(src.getHeight() * scale));
            BufferedImage thumb = new BufferedImage(w, h, BufferedImage.TYPE_INT_ARGB);
            var g = thumb.createGraphics();
            g.setRenderingHint(RenderingHints.KEY_INTERPOLATION, RenderingHints.VALUE_INTERPOLATION_BILINEAR);
            g.drawImage(src, 0, 0, w, h, null);
            g.dispose();
            Path out = thumbnailPath(name);
            ImageIO.write(thumb, "png", out.toFile());
            return out;
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    public Path loadThumbnail(String name) {
        load(name);
        Path thumb = thumbnailPath(name);
        if (!Files.isRegularFile(thumb)) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "No thumbnail yet for: " + name);
        }
        return thumb;
    }

    private Path thumbnailPath(String name) {
        return thumbnails.resolve(name + ".png");
    }

    private FileInfo info(Path file) {
        try {
            return new FileInfo(file.getFileName().toString(), Files.size(file),
                    Files.getLastModifiedTime(file).toInstant());
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    /** Rejects anything that is not a plain file name (path separators, dot names, reserved name). */
    private static String validate(String name) {
        if (name == null || name.isBlank() || name.equals(".") || name.equals("..")
                || name.contains("/") || name.contains("\\") || name.indexOf('\0') >= 0
                || name.equals(THUMBNAIL_DIR)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Invalid file name");
        }
        return name;
    }
}
