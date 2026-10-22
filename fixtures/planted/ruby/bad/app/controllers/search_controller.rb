# frozen_string_literal: true

# CWE-79: the query is marked html_safe and rendered raw.
class SearchController < ApplicationController
  def index
    render html: "<h1>Results for #{params[:q]}</h1>".html_safe # expect-ext: semgrep/semgrep-rules.ruby.rails.security.audit.xss.avoid-html-safe, semgrep/semgrep-rules.ruby.rails.security.injection.raw-html-format
  end
end
