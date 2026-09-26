import QRCode from "qrcode"

const DEMO_VPA = "demo@upi"

export async function GET(request: Request) {
  const value = new URL(request.url).searchParams.get("uri")

  if (!value || value.length > 500) {
    return Response.json({ error: "A valid demo UPI URI is required." }, { status: 400 })
  }

  let paymentUri: URL

  try {
    paymentUri = new URL(value)
  } catch {
    return Response.json({ error: "The UPI URI is invalid." }, { status: 400 })
  }

  const amountText = paymentUri.searchParams.get("am")
  const amount = Number(amountText)
  const isValidDemoPayment =
    amountText !== null && /^\d+(\.\d{1,2})?$/.test(amountText) &&
    paymentUri.protocol === "upi:" &&
    paymentUri.hostname === "pay" &&
    paymentUri.searchParams.get("pa") === DEMO_VPA &&
    paymentUri.searchParams.get("cu") === "INR" &&
    Number.isFinite(amount) &&
    amount > 0 &&
    amount <= 10000000

  if (!isValidDemoPayment) {
    return Response.json({ error: "Only valid JalSetu demo payment links can be rendered." }, { status: 400 })
  }

  try {
    const svg = await QRCode.toString(value, {
      type: "svg",
      width: 240,
      margin: 0,
      errorCorrectionLevel: "M",
      color: { dark: "#163138", light: "#ffffff00" },
    })

    return new Response(svg, {
      headers: {
        "Content-Type": "image/svg+xml; charset=utf-8",
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
      },
    })
  } catch {
    return Response.json({ error: "The QR code could not be generated." }, { status: 500 })
  }
}
