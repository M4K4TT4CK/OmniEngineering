import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import javax.imageio.ImageIO;
import javax.imageio.ImageReader;
import javax.imageio.stream.ImageInputStream;
import java.awt.Graphics2D;
import java.awt.RenderingHints;
import java.awt.image.BufferedImage;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.URLDecoder;
import java.net.UnknownHostException;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Duration;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.Iterator;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.Executors;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Thumbnail service. POST an image (raw body) or a URL (JSON) plus a list of widths and get back
 * URLs of PNG thumbnails. Thumbnails are rendered the first time a width is requested and cached
 * on disk at {cacheDir}/{sourceId}/{width}.png; GET serves them straight from that cache.
 *
 * Run: java ThumbnailService [port] [cacheDir]
 */
public final class ThumbnailService {

    static final int MAX_WIDTH = 4096;
    static final int MAX_WIDTHS = 10;
    static final int MAX_BYTES = 20 * 1024 * 1024;
    // Decompression-bomb guard: reject sources whose decoded size would be too large.
    static final long MAX_PIXELS = 25_000_000L;
    static final Duration FETCH_TIMEOUT = Duration.ofSeconds(10);

    private static final Pattern THUMBNAIL_PATH = Pattern.compile("/thumbnails/([0-9a-f]{64})/(\\d{1,4})\\.png");

    private final Path cacheDir;
    private final boolean allowPrivateHosts;
    private final ObjectMapper json = new ObjectMapper();
    private final HttpClient http = HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(5))
            .followRedirects(HttpClient.Redirect.NEVER)
            .build();

    /**
     * @param allowPrivateHosts whether URL sources may point at loopback/private addresses.
     *                          Keep this false outside of tests.
     */
    public ThumbnailService(Path cacheDir, boolean allowPrivateHosts) throws IOException {
        this.cacheDir = Files.createDirectories(cacheDir);
        this.allowPrivateHosts = allowPrivateHosts;
    }

    public static void main(String[] args) throws IOException {
        int port = args.length > 0 ? Integer.parseInt(args[0]) : 8080;
        Path cacheDir = Path.of(args.length > 1 ? args[1] : "thumbnail-cache");
        new ThumbnailService(cacheDir, false).start(new InetSocketAddress(port));
        System.out.println("thumbnail service on port " + port + ", cache " + cacheDir.toAbsolutePath());
    }

    public HttpServer start(InetSocketAddress address) throws IOException {
        HttpServer server = HttpServer.create(address, 0);
        server.createContext("/", this::handle);
        server.setExecutor(Executors.newFixedThreadPool(8));
        server.start();
        return server;
    }

    void handle(HttpExchange ex) throws IOException {
        try {
            route(ex);
        } catch (IllegalArgumentException e) {
            sendJson(ex, 400, Map.of("error", e.getMessage()));
        } catch (IOException | RuntimeException e) {
            System.err.println("internal error: " + e);
            sendJson(ex, 500, Map.of("error", "internal error"));
        }
    }

    private void route(HttpExchange ex) throws IOException {
        String method = ex.getRequestMethod();
        String path = ex.getRequestURI().getPath();
        Matcher thumbnail = THUMBNAIL_PATH.matcher(path);
        if ("POST".equals(method) && "/thumbnails".equals(path)) {
            create(ex);
        } else if ("GET".equals(method) && thumbnail.matches()) {
            serve(ex, thumbnail.group(1), Integer.parseInt(thumbnail.group(2)));
        } else {
            sendJson(ex, 404, Map.of("error", "not found"));
        }
    }

    private void create(HttpExchange ex) throws IOException {
        String type = mediaType(ex);
        String id;
        List<Integer> widths;
        ImageLoader loader;
        if ("application/json".equals(type)) {
            JsonNode body = readJson(ex);
            widths = validateWidths(parseWidthNodes(body.get("widths")));
            JsonNode url = body.get("url");
            if (url == null || !url.isTextual()) {
                throw new IllegalArgumentException("'url' must be a string");
            }
            String source = url.asText();
            id = sha256Hex(source.getBytes(StandardCharsets.UTF_8));
            loader = () -> fetch(source);
        } else if (type.startsWith("image/")) {
            widths = validateWidths(parseWidthList(queryParam(ex, "widths")));
            byte[] bytes = readBody(ex);
            id = sha256Hex(bytes);
            loader = () -> bytes;
        } else {
            throw new IllegalArgumentException("send application/json with a 'url', or an image/* body");
        }

        ensureThumbnails(id, widths, loader);

        List<Map<String, Object>> thumbnails = new ArrayList<>();
        for (int width : widths) {
            thumbnails.add(Map.of("width", width, "url", baseUrl(ex) + "/thumbnails/" + id + "/" + width + ".png"));
        }
        sendJson(ex, 200, Map.of("id", id, "thumbnails", thumbnails));
    }

    private void serve(HttpExchange ex, String id, int width) throws IOException {
        Path file = thumbnailPath(id, width);
        if (!Files.isRegularFile(file)) {
            sendJson(ex, 404, Map.of("error", "thumbnail not found"));
            return;
        }
        ex.getResponseHeaders().set("Cache-Control", "public, max-age=3600");
        sendBytes(ex, 200, "image/png", Files.readAllBytes(file));
    }

    /** Renders every requested width that is not already cached. The loader runs only if something is missing. */
    private void ensureThumbnails(String id, List<Integer> widths, ImageLoader loader) throws IOException {
        List<Integer> missing = widths.stream()
                .filter(w -> !Files.isRegularFile(thumbnailPath(id, w)))
                .toList();
        if (missing.isEmpty()) {
            return;
        }
        BufferedImage original = decode(loader.load());
        Path dir = Files.createDirectories(cacheDir.resolve(id));
        for (int width : missing) {
            byte[] png = render(original, width);
            // Write to a temp file and move it into place so readers never see a partial thumbnail.
            Path tmp = Files.createTempFile(dir, "thumb-", ".part");
            Files.write(tmp, png);
            Files.move(tmp, thumbnailPath(id, width), StandardCopyOption.ATOMIC_MOVE);
        }
    }

    private Path thumbnailPath(String id, int width) {
        return cacheDir.resolve(id).resolve(width + ".png");
    }

    static BufferedImage decode(byte[] source) {
        try (ImageInputStream in = ImageIO.createImageInputStream(new ByteArrayInputStream(source))) {
            Iterator<ImageReader> readers = ImageIO.getImageReaders(in);
            if (!readers.hasNext()) {
                throw new IllegalArgumentException("not a supported image format");
            }
            ImageReader reader = readers.next();
            try {
                reader.setInput(in);
                if ((long) reader.getWidth(0) * reader.getHeight(0) > MAX_PIXELS) {
                    throw new IllegalArgumentException("image has too many pixels");
                }
                return reader.read(0);
            } finally {
                reader.dispose();
            }
        } catch (IOException e) {
            throw new IllegalArgumentException("not a readable image", e);
        }
    }

    static byte[] render(BufferedImage original, int width) throws IOException {
        int height = Math.max(1, (int) Math.round((double) original.getHeight() * width / original.getWidth()));
        BufferedImage out = new BufferedImage(width, height, BufferedImage.TYPE_INT_ARGB);
        Graphics2D g = out.createGraphics();
        try {
            g.setRenderingHint(RenderingHints.KEY_INTERPOLATION, RenderingHints.VALUE_INTERPOLATION_BILINEAR);
            g.drawImage(original, 0, 0, width, height, null);
        } finally {
            g.dispose();
        }
        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        ImageIO.write(out, "png", bytes);
        return bytes.toByteArray();
    }

    private byte[] fetch(String url) throws IOException {
        HttpRequest request = HttpRequest.newBuilder(checkUrl(url)).timeout(FETCH_TIMEOUT).GET().build();
        HttpResponse<InputStream> response;
        try {
            response = http.send(request, HttpResponse.BodyHandlers.ofInputStream());
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IOException("interrupted while fetching image", e);
        } catch (IOException e) {
            throw new IllegalArgumentException("could not fetch image URL: " + e.getMessage(), e);
        }
        try (InputStream in = response.body()) {
            if (response.statusCode() != 200) {
                throw new IllegalArgumentException("image URL returned HTTP " + response.statusCode());
            }
            return readLimited(in, MAX_BYTES);
        }
    }

    /** Only http(s) URLs are allowed; unless private hosts are enabled, the host must resolve to a public address. */
    private URI checkUrl(String raw) {
        URI uri;
        try {
            uri = URI.create(raw);
        } catch (IllegalArgumentException e) {
            throw new IllegalArgumentException("'url' is not a valid URI");
        }
        String scheme = uri.getScheme();
        if (!("http".equals(scheme) || "https".equals(scheme)) || uri.getHost() == null) {
            throw new IllegalArgumentException("'url' must be an absolute http or https URL");
        }
        if (!allowPrivateHosts) {
            try {
                for (InetAddress address : InetAddress.getAllByName(uri.getHost())) {
                    if (address.isLoopbackAddress() || address.isSiteLocalAddress() || address.isLinkLocalAddress()
                            || address.isAnyLocalAddress() || address.isMulticastAddress()) {
                        throw new IllegalArgumentException("'url' host is not publicly routable");
                    }
                }
            } catch (UnknownHostException e) {
                throw new IllegalArgumentException("'url' host could not be resolved");
            }
        }
        return uri;
    }

    private JsonNode readJson(HttpExchange ex) throws IOException {
        try {
            return json.readTree(readBody(ex));
        } catch (JsonProcessingException e) {
            throw new IllegalArgumentException("request body is not valid JSON");
        }
    }

    static List<Integer> parseWidthNodes(JsonNode node) {
        if (node == null || !node.isArray()) {
            throw new IllegalArgumentException("'widths' must be an array of integers");
        }
        List<Integer> widths = new ArrayList<>();
        for (JsonNode width : node) {
            if (!width.isInt()) {
                throw new IllegalArgumentException("'widths' must be an array of integers");
            }
            widths.add(width.intValue());
        }
        return widths;
    }

    static List<Integer> parseWidthList(String csv) {
        List<Integer> widths = new ArrayList<>();
        for (String part : csv.split(",")) {
            if (part.isBlank()) {
                continue;
            }
            try {
                widths.add(Integer.parseInt(part.trim()));
            } catch (NumberFormatException e) {
                throw new IllegalArgumentException("'widths' must be comma-separated integers");
            }
        }
        return widths;
    }

    static List<Integer> validateWidths(List<Integer> widths) {
        if (widths.isEmpty() || widths.size() > MAX_WIDTHS) {
            throw new IllegalArgumentException("provide between 1 and " + MAX_WIDTHS + " widths");
        }
        for (int width : widths) {
            if (width < 1 || width > MAX_WIDTH) {
                throw new IllegalArgumentException("widths must be between 1 and " + MAX_WIDTH);
            }
        }
        return List.copyOf(new LinkedHashSet<>(widths));
    }

    static byte[] readLimited(InputStream in, int max) throws IOException {
        byte[] data = in.readNBytes(max + 1);
        if (data.length > max) {
            throw new IllegalArgumentException("image exceeds " + max + " bytes");
        }
        return data;
    }

    private static byte[] readBody(HttpExchange ex) throws IOException {
        return readLimited(ex.getRequestBody(), MAX_BYTES);
    }

    private static String queryParam(HttpExchange ex, String name) {
        String query = ex.getRequestURI().getRawQuery();
        if (query == null) {
            return "";
        }
        for (String pair : query.split("&")) {
            String[] kv = pair.split("=", 2);
            if (kv.length == 2 && kv[0].equals(name)) {
                return URLDecoder.decode(kv[1], StandardCharsets.UTF_8);
            }
        }
        return "";
    }

    private static String mediaType(HttpExchange ex) {
        String type = ex.getRequestHeaders().getFirst("Content-Type");
        return type == null ? "" : type.split(";")[0].trim().toLowerCase(Locale.ROOT);
    }

    private static String baseUrl(HttpExchange ex) {
        String host = ex.getRequestHeaders().getFirst("Host");
        return "http://" + (host != null ? host : "localhost");
    }

    static String sha256Hex(byte[] data) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(data));
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }

    private void sendJson(HttpExchange ex, int status, Object body) throws IOException {
        sendBytes(ex, status, "application/json", json.writeValueAsBytes(body));
    }

    private static void sendBytes(HttpExchange ex, int status, String type, byte[] body) throws IOException {
        ex.getResponseHeaders().set("Content-Type", type);
        ex.sendResponseHeaders(status, body.length);
        try (OutputStream out = ex.getResponseBody()) {
            out.write(body);
        }
    }

    @FunctionalInterface
    interface ImageLoader {
        byte[] load() throws IOException;
    }
}
