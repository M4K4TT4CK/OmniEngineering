# frozen_string_literal: true

# CWE-434: a type allowlist, a size cap and a server-chosen name outside public/.
class AvatarsController < ApplicationController
  ALLOWED = { "image/png" => ".png", "image/jpeg" => ".jpg" }.freeze

  def create
    upload = params.require(:avatar)
    ext = ALLOWED[Marcel::MimeType.for(upload, name: upload.original_filename)]
    return head :unsupported_media_type if ext.nil? || upload.size > 2.megabytes

    File.binwrite(Rails.root.join("storage", "avatars", "#{SecureRandom.uuid}#{ext}"), upload.read)
    head :created
  end
end
