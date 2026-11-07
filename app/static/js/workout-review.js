'use strict';
const reviewForm = document.querySelector('[data-workout-review]');
if (reviewForm) {
  const button = reviewForm.querySelector('button');
  const status = document.getElementById('review-status');
  const label = button.textContent;
  window.addEventListener('pageshow', () => {
    reviewForm.removeAttribute('aria-busy');
    button.disabled = false;
    button.textContent = label;
    status.hidden = true;
  });
  reviewForm.addEventListener('submit', (event) => {
    if (reviewForm.getAttribute('aria-busy') === 'true') {
      event.preventDefault();
      return;
    }
    reviewForm.setAttribute('aria-busy', 'true');
    button.disabled = true;
    button.textContent = 'Reviewing…';
    status.hidden = false;
  });
}
