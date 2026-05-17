'use client'
import { useEffect, useRef } from 'react'

export default function ScrollHint() {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const onScroll = () => {
      if (ref.current) ref.current.style.opacity = window.scrollY > 60 ? '0' : '1'
    }
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  return (
    <div ref={ref} className="scroll-hint" aria-hidden="true">
      &#8964;
    </div>
  )
}
