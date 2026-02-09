// =============================================
// PantiesFan.com - Main Application JavaScript
// =============================================

gsap.registerPlugin(ScrollTrigger);

// --- GSAP Animations ---

// CTA Reveal
gsap.from(".cta-content", {
    y: 50,
    opacity: 0,
    duration: 1,
    ease: "power2.out",
    scrollTrigger: {
        trigger: ".cta-section",
        start: "top 70%"
    }
});

// Hero Parallax
gsap.to(".hero-bg", {
    yPercent: 30,
    ease: "none",
    scrollTrigger: {
        trigger: ".hero",
        start: "top top",
        end: "bottom top",
        scrub: true
    }
});

// Hero Content Fade In
gsap.to(".hero p", {
    opacity: 1,
    y: 0,
    duration: 1.5,
    delay: 0.5,
    ease: "power3.out"
});

// Navigation scroll effect
window.addEventListener('scroll', () => {
    const nav = document.querySelector('nav');
    if (window.scrollY > 50) {
        nav.classList.add('scrolled');
    } else {
        nav.classList.remove('scrolled');
    }
});

// Staggered Flip In for Cards
gsap.from(".card", {
    y: 100,
    opacity: 0,
    duration: 0.8,
    stagger: 0.2,
    ease: "power2.out",
    scrollTrigger: {
        trigger: ".grid",
        start: "top 80%"
    }
});

// Text reveal for manifesto
gsap.from(".manifesto-text", {
    y: 50,
    opacity: 0,
    duration: 1,
    scrollTrigger: {
        trigger: ".manifesto",
        start: "top 75%"
    }
});

// How It Works Stagger
gsap.to(".hiw-step", {
    opacity: 1,
    y: 0,
    duration: 0.8,
    stagger: 0.3,
    ease: "power2.out",
    scrollTrigger: {
        trigger: ".how-it-works",
        start: "top 70%"
    }
});

// Packaging Stagger
gsap.to(".pack-item", {
    opacity: 1,
    y: 0,
    duration: 1,
    stagger: 0.2,
    ease: "power3.out",
    scrollTrigger: {
        trigger: ".packaging-section",
        start: "top 70%"
    }
});


// =============================================
// AUCTION COUNTDOWN SYSTEM
// =============================================

function initCountdowns() {
    const countdownElements = document.querySelectorAll('[data-ends-at]');

    countdownElements.forEach(el => {
        updateCountdown(el);
    });

    // Update every second
    setInterval(() => {
        countdownElements.forEach(el => {
            updateCountdown(el);
        });
    }, 1000);
}

function updateCountdown(el) {
    const endsAt = new Date(el.dataset.endsAt).getTime();
    const now = Date.now();
    const diff = endsAt - now;

    if (diff <= 0) {
        el.innerHTML = '<span style="color: #999;">Ended</span>';
        // Disable bid button for this card
        const card = el.closest('.card');
        if (card) {
            const bidBtn = card.querySelector('.bid-btn');
            const bidInput = card.querySelector('.bid-amount-input');
            if (bidBtn) {
                bidBtn.disabled = true;
                bidBtn.innerText = 'Auction Ended';
            }
            if (bidInput) {
                bidInput.disabled = true;
            }
            // Swap badge
            const liveBadge = card.querySelector('.live-badge');
            if (liveBadge) {
                liveBadge.outerHTML = '<div class="ended-badge">Ended</div>';
            }
        }
        return;
    }

    const days = Math.floor(diff / (1000 * 60 * 60 * 24));
    const hours = Math.floor((diff % (1000 * 60 * 60 * 24)) / (1000 * 60 * 60));
    const minutes = Math.floor((diff % (1000 * 60 * 60)) / (1000 * 60));
    const seconds = Math.floor((diff % (1000 * 60)) / 1000);

    let display = '';
    if (days > 0) {
        display = `${days}d ${hours}h ${minutes}m`;
    } else if (hours > 0) {
        display = `${hours}h ${minutes}m ${seconds}s`;
    } else if (minutes > 0) {
        display = `${minutes}m ${seconds}s`;
    } else {
        display = `${seconds}s`;
    }

    // Turn red if under 5 minutes
    if (diff < 300000) {
        el.style.color = '#ff4444';
        el.style.fontWeight = '600';
    }

    el.textContent = display;
}


// =============================================
// BIDDING SYSTEM
// =============================================

async function placeBid(itemId) {
    const card = document.getElementById(`card-${itemId}`);
    const btn = card.querySelector('.bid-btn');
    const input = card.querySelector('.bid-amount-input');
    const priceEl = document.getElementById(`price-${itemId}`);
    const bidderEl = document.getElementById(`bidder-${itemId}`);
    const countdownEl = card.querySelector('[data-ends-at]');
    const historyList = document.getElementById(`bid-history-${itemId}`);
    const minHint = document.getElementById(`min-bid-${itemId}`);

    const bidAmount = parseFloat(input.value);

    if (isNaN(bidAmount) || bidAmount <= 0) {
        showFlash('Please enter a valid bid amount.', 'error');
        return;
    }

    // Visual Feedback
    const originalText = btn.innerText;
    btn.innerText = "Bidding...";
    btn.disabled = true;
    input.disabled = true;

    try {
        const response = await fetch(`/api/bid/${itemId}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ amount: bidAmount })
        });

        const data = await response.json();

        if (data.success) {
            // Flash price update
            priceEl.style.color = "#fff";
            setTimeout(() => priceEl.style.color = "var(--accent-gold)", 500);

            priceEl.innerText = data.new_price;
            bidderEl.innerText = "Last: " + data.bidder;

            // Update countdown if sniper extension happened
            if (data.ends_at && countdownEl) {
                countdownEl.dataset.endsAt = data.ends_at;
            }

            // Update min bid hint
            if (data.min_next_bid && minHint) {
                minHint.textContent = `Min bid: $${data.min_next_bid}`;
            }

            // Clear input and set new placeholder
            input.value = '';
            if (data.min_next_bid) {
                input.placeholder = `$${data.min_next_bid}+`;
            }

            // Update bid history
            if (data.recent_bids && historyList) {
                historyList.innerHTML = '';
                data.recent_bids.forEach(bid => {
                    const li = document.createElement('li');
                    li.innerHTML = `<span>${bid.bidder}</span><span class="bid-amount">$${bid.amount}</span>`;
                    historyList.appendChild(li);
                });
            }

            btn.innerText = "Bid Placed!";
            btn.style.background = "rgba(76, 175, 80, 0.3)";
            setTimeout(() => {
                btn.innerText = originalText;
                btn.disabled = false;
                input.disabled = false;
                btn.style.background = "";
            }, 2000);
        } else {
            showFlash(data.message || 'Bid failed', 'error');
            btn.innerText = originalText;
            btn.disabled = false;
            input.disabled = false;
        }
    } catch (error) {
        console.error('Error:', error);
        showFlash('Network error. Please try again.', 'error');
        btn.innerText = originalText;
        btn.disabled = false;
        input.disabled = false;
    }
}

// Allow Enter key to place bid
document.addEventListener('keydown', function(e) {
    if (e.key === 'Enter' && e.target.classList.contains('bid-amount-input')) {
        const card = e.target.closest('.card');
        if (card) {
            const itemId = card.id.replace('card-', '');
            placeBid(itemId);
        }
    }
});


// =============================================
// FLASH MESSAGES
// =============================================

function showFlash(message, type) {
    // Create flash container if not exists
    let container = document.querySelector('.flash-messages');
    if (!container) {
        container = document.createElement('div');
        container.className = 'flash-messages';
        container.style.position = 'fixed';
        container.style.top = '100px';
        container.style.right = '20px';
        container.style.zIndex = '9999';
        container.style.maxWidth = '400px';
        document.body.appendChild(container);
    }

    const flash = document.createElement('div');
    flash.className = `flash-message ${type}`;
    flash.textContent = message;
    container.appendChild(flash);

    setTimeout(() => {
        flash.style.opacity = '0';
        flash.style.transition = 'opacity 0.3s ease';
        setTimeout(() => flash.remove(), 300);
    }, 3000);
}


// =============================================
// INIT
// =============================================

document.addEventListener('DOMContentLoaded', function() {
    initCountdowns();
});
