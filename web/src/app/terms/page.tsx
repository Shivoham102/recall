import type { Metadata } from 'next'
import Nav from '@/components/Nav'
import Footer from '@/components/Footer'

export const metadata: Metadata = {
  title: 'Terms of Service - recall',
}

export default function Terms() {
  return (
    <>
      <Nav />
      <main className="legal-wrap">
        <p className="legal-label">Legal</p>
        <h1>Terms of Service</h1>
        <p className="legal-date">Effective date: May 2025</p>

        <p>
          By downloading, installing, or using Recall, you agree to these Terms of Service. Please
          read them carefully.
        </p>

        <h2>1. Beta software</h2>
        <p>
          Recall is currently in beta. Features may change, data may be lost, and the service may
          be interrupted without notice. Use it for personal productivity and understand that it is
          not yet production-grade software.
        </p>

        <h2>2. Eligibility</h2>
        <p>
          You must be at least 13 years old to use Recall. By using the app, you represent that you
          meet this requirement.
        </p>

        <h2>3. Your Google account</h2>
        <p>
          Recall requires access to your Google account (Gmail and Calendar). You are responsible
          for maintaining the security of your Google account. Recall will only read data from your
          account; it will not send emails or modify calendar events on your behalf without your
          explicit action.
        </p>
        <p>
          You can revoke Recall&apos;s access at any time via{' '}
          <a
            href="https://myaccount.google.com/permissions"
            target="_blank"
            rel="noopener noreferrer"
          >
            Google Account Permissions
          </a>
          .
        </p>

        <h2>4. Acceptable use</h2>
        <p>You agree not to:</p>
        <ul>
          <li>Use Recall to violate any applicable law or regulation</li>
          <li>
            Attempt to reverse-engineer, decompile, or extract the source code beyond what is
            publicly available on GitHub
          </li>
          <li>
            Use Recall in any way that could harm, disable, or impair our services or other users
          </li>
        </ul>

        <h2>5. Disclaimer of warranties</h2>
        <p>
          Recall is provided &quot;as is&quot; and &quot;as available&quot; without warranties of
          any kind, express or implied. We do not warrant that the service will be uninterrupted,
          error-free, or that data will not be lost. Use at your own risk.
        </p>

        <h2>6. Limitation of liability</h2>
        <p>
          To the fullest extent permitted by law, Recall and its creator (Shivoham Angal) shall not
          be liable for any indirect, incidental, special, or consequential damages arising from
          your use of the app, including loss of data or missed tasks.
        </p>

        <h2>7. Open source</h2>
        <p>
          The Recall source code is publicly available on{' '}
          <a
            href="https://github.com/Shivoham102/recall"
            target="_blank"
            rel="noopener noreferrer"
          >
            GitHub
          </a>
          . Use of the source code is subject to the terms of the repository&apos;s license.
        </p>

        <h2>8. Changes to these terms</h2>
        <p>
          We may update these Terms as the app evolves. Continued use of Recall after changes
          constitutes acceptance of the updated Terms. The effective date at the top of this page
          reflects the most recent revision.
        </p>

        <h2>9. Governing law</h2>
        <p>
          These Terms are governed by the laws of the State of California, United States, without
          regard to conflict-of-law principles.
        </p>

        <h2>10. Contact</h2>
        <p>
          Questions about these Terms? Contact Shivoham Angal at{' '}
          <a href="mailto:shivohamangal@gmail.com">shivohamangal@gmail.com</a>.
        </p>
      </main>
      <Footer />
    </>
  )
}
