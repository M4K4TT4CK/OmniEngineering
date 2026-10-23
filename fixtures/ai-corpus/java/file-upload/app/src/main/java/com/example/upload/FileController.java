package com.example.upload;

import java.io.IOException;
import java.net.URI;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

import org.springframework.core.io.FileSystemResource;
import org.springframework.core.io.Resource;
import org.springframework.http.ContentDisposition;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.util.UriUtils;

@RestController
@RequestMapping("/files")
public class FileController {

    private final FileStorage storage;

    public FileController(FileStorage storage) {
        this.storage = storage;
    }

    @PostMapping(consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    public ResponseEntity<FileStorage.FileInfo> upload(@RequestParam("file") MultipartFile file) {
        FileStorage.FileInfo info = storage.store(file);
        URI location = URI.create("/files/" + UriUtils.encodePathSegment(info.name(), "UTF-8"));
        return ResponseEntity.created(location).body(info);
    }

    @GetMapping
    public List<FileStorage.FileInfo> list() {
        return storage.list();
    }

    @GetMapping("/{name}")
    public ResponseEntity<Resource> download(@PathVariable String name) throws IOException {
        Path file = storage.load(name);
        return ResponseEntity.ok()
                .header(HttpHeaders.CONTENT_DISPOSITION,
                        ContentDisposition.attachment().filename(name).build().toString())
                .contentType(contentType(file))
                .body(new FileSystemResource(file));
    }

    @DeleteMapping("/{name}")
    public ResponseEntity<Void> delete(@PathVariable String name) {
        storage.delete(name);
        return ResponseEntity.noContent().build();
    }

    /** Generates a PNG thumbnail for an uploaded image and returns it. */
    @PostMapping("/{name}/thumbnail")
    public ResponseEntity<Resource> createThumbnail(@PathVariable String name) {
        return png(storage.createThumbnail(name));
    }

    @GetMapping("/{name}/thumbnail")
    public ResponseEntity<Resource> thumbnail(@PathVariable String name) {
        return png(storage.loadThumbnail(name));
    }

    private static ResponseEntity<Resource> png(Path file) {
        return ResponseEntity.ok().contentType(MediaType.IMAGE_PNG).body(new FileSystemResource(file));
    }

    private static MediaType contentType(Path file) throws IOException {
        String type = Files.probeContentType(file);
        return type == null ? MediaType.APPLICATION_OCTET_STREAM : MediaType.parseMediaType(type);
    }
}
