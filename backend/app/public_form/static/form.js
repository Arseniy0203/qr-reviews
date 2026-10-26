// Prevent double submits on slow mobile networks. Disabling happens after the
// browser has collected the form data, so the clicked button's value is still sent.
document.querySelectorAll("form").forEach(function (form) {
  form.addEventListener("submit", function () {
    setTimeout(function () {
      form.querySelectorAll("button").forEach(function (b) { b.disabled = true; });
    }, 0);
  });
});
