function togglePassword() {
  const password = document.getElementById("password");
  const button = password.nextElementSibling;
  password.type = password.type === "password" ? "text" : "password";
  button.textContent = password.type === "password" ? "Show" : "Hide";
}

function filterRows(value) {
  document.querySelectorAll("#claimRows tr").forEach((row) => {
    row.style.display = row.textContent.toLowerCase().includes(value.toLowerCase()) ? "" : "none";
  });
}

function filterStatus(value) {
  document.querySelectorAll("#claimRows tr[data-status]").forEach((row) => {
    row.style.display = !value || row.dataset.status === value ? "" : "none";
  });
}

document.querySelectorAll(".dropzone input").forEach((input) => {
  input.addEventListener("change", () => {
    const file = input.files[0];
    if (!file) return;
    const dropzone = input.closest(".dropzone");
    dropzone.classList.add("selected");
    dropzone.querySelector("em").textContent = file.name;
  });
});

const analyseReceiptButton = document.getElementById("analyseReceipt");

if (analyseReceiptButton) {
  analyseReceiptButton.addEventListener("click", async () => {
    const form = analyseReceiptButton.closest("form");
    const receipt = form.querySelector('[name="supporting_document"]');
    const status = document.getElementById("aiReceiptStatus");

    if (!receipt.files[0]) {
      status.className = "ai-status error";
      status.textContent = "Choose a supporting document first.";
      return;
    }

    analyseReceiptButton.disabled = true;
    analyseReceiptButton.textContent = "Analysing…";
    status.className = "ai-status loading";
    status.textContent = "Sending the receipt to Gemini for analysis…";

    try {
      const body = new FormData();
      body.append("receipt", receipt.files[0]);
      const response = await fetch(analyseReceiptButton.dataset.url, { method: "POST", body });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Receipt analysis failed.");

      const setValue = (name, value) => {
        const field = form.elements[name];
        if (field && value !== "" && value !== 0 && value !== null) field.value = value;
      };
      setValue("title", data.title);
      setValue("amount", data.amount);
      setValue("purchase_date", data.purchase_date);
      setValue("merchant", data.merchant);
      setValue("category", data.category);
      setValue("description", data.description);

      const confidence = Math.round((data.confidence || 0) * 100);
      const receiptNumber = data.receipt_number ? ` · Receipt ${data.receipt_number}` : "";
      status.className = "ai-status success";
      status.textContent = `Suggestions added · ${confidence}% confidence${receiptNumber}. Please verify every field.`;
    } catch (error) {
      status.className = "ai-status error";
      status.textContent = `${error.message} You can continue manually.`;
    } finally {
      analyseReceiptButton.disabled = false;
      analyseReceiptButton.textContent = "Analyse receipt";
    }
  });
}
