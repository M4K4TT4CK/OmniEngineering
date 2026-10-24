# frozen_string_literal: true

# CWE-200/209: a catch-all rescue hands the exception's text to the client.
class PaymentsController < ApplicationController
  def create
    charge = Billing.charge!(current_user, params.require(:amount).to_i)
    render json: { id: charge.id }, status: :created
  rescue => e
    render json: { error: e.message }, status: :internal_server_error # expect: arbiter/ruby.error-message-to-client
  end
end
