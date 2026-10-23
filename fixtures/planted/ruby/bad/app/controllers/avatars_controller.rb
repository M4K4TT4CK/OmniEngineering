# frozen_string_literal: true

# CWE-434: any upload is written into public/ under the client's filename.
class AvatarsController < ApplicationController
  def create
    upload = params.require(:avatar)
    File.binwrite(Rails.root.join("public", "uploads", upload.original_filename), upload.read) # expect: arbiter/ruby.upload-client-filename
    head :created
  end
end
