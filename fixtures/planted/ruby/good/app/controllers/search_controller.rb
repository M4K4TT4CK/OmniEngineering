# frozen_string_literal: true

# CWE-79: the query is rendered through the tag helper, which escapes it.
class SearchController < ApplicationController
  def index
    render html: helpers.tag.h1("Results for #{params[:q]}")
  end
end
