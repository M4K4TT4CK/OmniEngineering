package com.example.upload;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

import java.awt.image.BufferedImage;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import javax.imageio.ImageIO;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;

@SpringBootTest
@AutoConfigureMockMvc
class FileUploadApplicationTests {

    @TempDir
    static Path tempDir;

    @DynamicPropertySource
    static void props(DynamicPropertyRegistry registry) {
        registry.add("app.storage-dir", () -> tempDir.toString());
        registry.add("app.thumbnail-size", () -> "32");
    }

    @Autowired
    MockMvc mvc;

    @BeforeEach
    void clean() throws Exception {
        // delete everything through the API so each test starts empty
        var body = mvc.perform(get("/files")).andReturn().getResponse().getContentAsString();
        var matcher = java.util.regex.Pattern.compile("\"name\":\"([^\"]+)\"").matcher(body);
        while (matcher.find()) {
            mvc.perform(delete("/files/" + matcher.group(1))).andExpect(status().isNoContent());
        }
    }

    private static MockMultipartFile file(String name, byte[] content) {
        return new MockMultipartFile("file", name, "application/octet-stream", content);
    }

    private static byte[] png(int w, int h) throws IOException {
        var out = new ByteArrayOutputStream();
        ImageIO.write(new BufferedImage(w, h, BufferedImage.TYPE_INT_RGB), "png", out);
        return out.toByteArray();
    }

    @Test
    void uploadThenListShowsSizeAndTime() throws Exception {
        mvc.perform(multipart("/files").file(file("a.txt", "hello".getBytes())))
                .andExpect(status().isCreated())
                .andExpect(header().string("Location", "/files/a.txt"))
                .andExpect(jsonPath("$.name").value("a.txt"));

        mvc.perform(get("/files"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.length()").value(1))
                .andExpect(jsonPath("$[0].name").value("a.txt"))
                .andExpect(jsonPath("$[0].size").value(5))
                .andExpect(jsonPath("$[0].uploadedAt").exists());
    }

    @Test
    void downloadReturnsContent() throws Exception {
        mvc.perform(multipart("/files").file(file("a.txt", "hello".getBytes())));

        var res = mvc.perform(get("/files/a.txt"))
                .andExpect(status().isOk())
                .andExpect(header().string("Content-Disposition", org.hamcrest.Matchers.containsString("a.txt")))
                .andReturn().getResponse();
        assertThat(res.getContentAsByteArray()).isEqualTo("hello".getBytes());
    }

    @Test
    void duplicateUploadIsConflict() throws Exception {
        mvc.perform(multipart("/files").file(file("a.txt", "one".getBytes()))).andExpect(status().isCreated());
        mvc.perform(multipart("/files").file(file("a.txt", "two".getBytes()))).andExpect(status().isConflict());
        assertThat(Files.readString(tempDir.resolve("a.txt"))).isEqualTo("one");
    }

    @Test
    void deleteRemovesFile() throws Exception {
        mvc.perform(multipart("/files").file(file("a.txt", "x".getBytes())));
        mvc.perform(delete("/files/a.txt")).andExpect(status().isNoContent());
        mvc.perform(get("/files/a.txt")).andExpect(status().isNotFound());
        mvc.perform(delete("/files/a.txt")).andExpect(status().isNotFound());
    }

    @Test
    void unsafeNamesAreRejected() throws Exception {
        mvc.perform(multipart("/files").file(file("../evil.txt", "x".getBytes())))
                .andExpect(status().isBadRequest());
        mvc.perform(multipart("/files").file(file("thumbnails", "x".getBytes())))
                .andExpect(status().isBadRequest());
        mvc.perform(get("/files/..")).andExpect(status().is4xxClientError());
        assertThat(tempDir.getParent().resolve("evil.txt")).doesNotExist();
    }

    @Test
    void thumbnailIsGeneratedAndScaledDown() throws Exception {
        mvc.perform(multipart("/files").file(file("pic.png", png(200, 100))));

        mvc.perform(get("/files/pic.png/thumbnail")).andExpect(status().isNotFound());

        var bytes = mvc.perform(post("/files/pic.png/thumbnail"))
                .andExpect(status().isOk())
                .andExpect(content().contentType("image/png"))
                .andReturn().getResponse().getContentAsByteArray();
        BufferedImage thumb = ImageIO.read(new ByteArrayInputStream(bytes));
        assertThat(thumb.getWidth()).isEqualTo(32);
        assertThat(thumb.getHeight()).isEqualTo(16);

        mvc.perform(get("/files/pic.png/thumbnail")).andExpect(status().isOk());
        // thumbnails must not appear in the file listing
        mvc.perform(get("/files")).andExpect(jsonPath("$.length()").value(1));
    }

    @Test
    void thumbnailOfNonImageIsUnsupportedMediaType() throws Exception {
        mvc.perform(multipart("/files").file(file("a.txt", "not an image".getBytes())));
        mvc.perform(post("/files/a.txt/thumbnail")).andExpect(status().isUnsupportedMediaType());
    }

    @Test
    void thumbnailOfMissingFileIsNotFound() throws Exception {
        mvc.perform(post("/files/nope.png/thumbnail")).andExpect(status().isNotFound());
    }
}
