import Nav from '@/components/Nav'
import Footer from '@/components/Footer'
import DownloadButtons from '@/components/DownloadButtons'
import FeatureCards from '@/components/FeatureCards'
import ScrollHint from '@/components/ScrollHint'

export default function Home() {
  return (
    <>
      <div className="grid-wrap">
        <div className="grid" />
      </div>

      <Nav />

      <section className="hero">
        <div className="orb-wrap">
          <div className="orb-glow" />
          <div className="orb">
            <div className="orb-shimmer" />
          </div>
        </div>
        <div className="hero-content">
          <h1>Think Less. Remember Everything.</h1>
          <p className="hero-sub">Morning brief. Smart reminders. Zero inbox anxiety.</p>
          <DownloadButtons />
        </div>
        <ScrollHint />
      </section>

      <div className="section-sep" />

      <section>
        <div className="section-inner">
          <p className="section-label">Capabilities</p>
          <h2>What recall does for you</h2>
          <FeatureCards />
        </div>
      </section>

      <div className="section-sep" />

      <section>
        <div className="section-inner">
          <p className="section-label">Getting started</p>
          <h2>Up and running in minutes</h2>
          <div className="steps">
            <div className="step">
              <div className="step-num" aria-hidden="true">1</div>
              <div>
                <h3>Download and install</h3>
                <p>Get the app for your platform. No account setup required to install.</p>
              </div>
            </div>
            <div className="step">
              <div className="step-num" aria-hidden="true">2</div>
              <div>
                <h3>Sign in with Google</h3>
                <p>Grant access to Gmail and Calendar. Recall reads; it never sends on your behalf.</p>
              </div>
            </div>
            <div className="step">
              <div className="step-num" aria-hidden="true">3</div>
              <div>
                <h3>Let Recall run</h3>
                <p>Check in each morning. Your brief is ready before you start your day.</p>
              </div>
            </div>
          </div>
        </div>
      </section>

      <Footer />
    </>
  )
}
