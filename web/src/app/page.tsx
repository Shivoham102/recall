import Nav from '@/components/Nav'
import Footer from '@/components/Footer'
import HeroOrb from '@/components/HeroOrb'
import DownloadButtons from '@/components/DownloadButtons'
import FeatureCards from '@/components/FeatureCards'
import ScrollHint from '@/components/ScrollHint'

export default function Home() {
  return (
    <>
      <Nav />

      <section className="hero">
        <HeroOrb />
        <div className="hero-content">
          <h1>Type Less. Remember Everything.</h1>
          <p className="hero-sub">Morning brief. Smart reminders. Zero inbox anxiety.</p>
          <DownloadButtons />
        </div>
        <ScrollHint />
      </section>

      <div className="section-sep" />

      <section>
        <div className="section-inner">
          <p className="section-label">The orb</p>
          <h2>See what Recall is doing</h2>

          <div className="orb-flow">
            <div className="orb-flow__step">
              <div className="orb-cell"><HeroOrb size={72} state="idle" bare /></div>
              <h3>Idle</h3>
              <p>Resting and ready.</p>
            </div>
            <span className="orb-flow__arrow" aria-hidden="true">›</span>
            <div className="orb-flow__step">
              <div className="orb-cell"><HeroOrb size={72} state="listening" bare /></div>
              <h3>Listening</h3>
              <p>Hearing you out.</p>
            </div>
            <span className="orb-flow__arrow" aria-hidden="true">›</span>
            <div className="orb-flow__step">
              <div className="orb-cell"><HeroOrb size={72} state="thinking" bare /></div>
              <h3>Thinking</h3>
              <p>Working through it.</p>
            </div>
            <span className="orb-flow__arrow" aria-hidden="true">›</span>
            <div className="orb-flow__step">
              <div className="orb-cell"><HeroOrb size={72} state="speaking" bare /></div>
              <h3>Speaking</h3>
              <p>Answering back.</p>
            </div>
          </div>
        </div>
      </section>

      <div className="section-sep" />

      <section>
        <div className="section-inner">
          <p className="section-label">Capabilities</p>
          <h2>What Recall does for you</h2>
          <FeatureCards />
        </div>
      </section>

      <Footer />
    </>
  )
}
