// scripts/email.mjs
import nodemailer from "nodemailer";

export async function sendEmail(subject, text, html) {
  const user = process.env.GMAIL_USER;
  const pass = process.env.GMAIL_APP_PASSWORD;
  const to = process.env.TO_EMAIL;

  if (!user || !pass || !to) {
    throw new Error("Missing env vars: GMAIL_USER, GMAIL_APP_PASSWORD, TO_EMAIL");
  }

  const transporter = nodemailer.createTransport({
    service: "gmail",
    auth: { user, pass }
  });

  const info = await transporter.sendMail({
    from: user,
    to,
    subject,
    text: text || "",
    html: html || undefined
  });
}