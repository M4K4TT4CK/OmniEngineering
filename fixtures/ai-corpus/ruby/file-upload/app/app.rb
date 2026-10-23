require "sinatra/base"
require "json"
require "time"
require "fileutils"
require "mini_magick"

# Simple file upload service. Files live directly in settings.upload_dir;
# thumbnails live in a ".thumbnails" subdirectory. Valid file names never
# start with a dot, so the two can't collide.
class App < Sinatra::Base
  IMAGE_EXTENSIONS = %w[.png .jpg .jpeg .gif .webp .bmp .tiff].freeze
  NAME_PATTERN = /\A[A-Za-z0-9_][A-Za-z0-9_.\- ]{0,254}\z/
  THUMB_DIR = ".thumbnails".freeze

  set :upload_dir, File.expand_path(ENV.fetch("UPLOAD_DIR", "./uploads"))
  set :max_upload_bytes, Integer(ENV.fetch("MAX_UPLOAD_BYTES", 25 * 1024 * 1024))
  set :thumbnail_size, ENV.fetch("THUMBNAIL_SIZE", "200x200")
  set :show_exceptions, false
  set :raise_errors, false

  before do
    content_type :json
    headers "X-Content-Type-Options" => "nosniff"
    FileUtils.mkdir_p(thumb_dir)
  end

  # Upload: multipart form with a "file" field.
  post "/files" do
    upload = params[:file]
    halt 400, err("missing 'file' form field") unless upload.is_a?(Hash) && upload[:tempfile]

    name = valid_name!(File.basename(upload[:filename].to_s.tr("\\", "/")))
    tmp = upload[:tempfile]
    halt 413, err("file exceeds #{settings.max_upload_bytes} bytes") if tmp.size > settings.max_upload_bytes

    begin
      # EXCL makes the existence check and creation atomic.
      File.open(file_path(name), File::WRONLY | File::CREAT | File::EXCL, 0o644) do |out|
        tmp.rewind
        IO.copy_stream(tmp, out)
      end
    rescue Errno::EEXIST
      halt 409, err("file already exists")
    end

    status 201
    file_info(name).to_json
  end

  get "/files" do
    names = Dir.children(settings.upload_dir).select do |n|
      n.match?(NAME_PATTERN) && File.file?(file_path(n))
    end
    names.sort.map { |n| file_info(n) }.to_json
  end

  get "/files/:name" do
    name = existing!(params[:name])
    send_file file_path(name), filename: name, disposition: "attachment"
  end

  delete "/files/:name" do
    name = existing!(params[:name])
    File.delete(file_path(name))
    FileUtils.rm_f(thumb_path(name))
    status 204
  end

  # Generate (or regenerate) a thumbnail for an uploaded image.
  post "/files/:name/thumbnail" do
    name = existing!(params[:name])
    halt 415, err("not a supported image type") unless image?(name)

    begin
      MiniMagick.convert do |c|
        c << "#{file_path(name)}[0]" # first frame only, for animated formats
        c.auto_orient
        c.thumbnail "#{settings.thumbnail_size}>"
        c << "png:#{thumb_path(name)}"
      end
    rescue MiniMagick::Error
      FileUtils.rm_f(thumb_path(name))
      halt 422, err("could not process image")
    end

    status 201
    { name: name, thumbnail: "/files/#{Rack::Utils.escape_path(name)}/thumbnail" }.to_json
  end

  get "/files/:name/thumbnail" do
    name = existing!(params[:name])
    halt 404, err("thumbnail not generated") unless File.file?(thumb_path(name))
    send_file thumb_path(name), type: "image/png"
  end

  not_found { content_type :json; err("not found") }
  error(500) { content_type :json; err("internal error") }

  helpers do
    def err(message) = { error: message }.to_json

    def thumb_dir = File.join(settings.upload_dir, THUMB_DIR)

    def file_path(name) = File.join(settings.upload_dir, name)

    def thumb_path(name) = File.join(thumb_dir, "#{name}.png")

    def image?(name) = IMAGE_EXTENSIONS.include?(File.extname(name).downcase)

    def valid_name!(name)
      halt 400, err("invalid file name") unless name.match?(NAME_PATTERN) && !name.include?("..")
      name
    end

    def existing!(name)
      valid_name!(name)
      halt 404, err("file not found") unless File.file?(file_path(name))
      name
    end

    def file_info(name)
      stat = File.stat(file_path(name))
      {
        name: name,
        size: stat.size,
        uploaded_at: stat.mtime.utc.iso8601,
        thumbnail: File.file?(thumb_path(name))
      }
    end
  end
end
