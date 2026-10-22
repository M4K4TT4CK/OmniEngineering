# frozen_string_literal: true

# CWE-200/209: a declined card's message is for the user; anything else is logged and hidden.
class PaymentsController < ApplicationController
  def create
    charge = Billing.charge!(current_user, params.require(:amount).to_i)
    render json: { id: charge.id }, status: :created
  rescue Billing::CardDeclined => e
    render json: { error: e.message }, status: :payment_required
  rescue => e
    Rails.logger.error("payment failed: #{e.class}: #{e.message}")
    render json: { error: "payment failed" }, status: :internal_server_error
  end
end
