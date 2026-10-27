// app/static/js/client/cart.js
function cartData(qrToken) {
    return {
        qrToken,
        cartItems: [],
        cartCount: 0,
        cartTotal: 0,
        showModal: false,
        showCartModal: false,
        isSubmitting: false,
        currentCategory: '0',
        specialInstruction: '',

        // 같은 메뉴라도 옵션 조합이 다르면 별도 라인으로 취급
        _key(item) {
            const opts = (item.options || []).map(o => o.name).sort().join('|');
            return `${item.itemId}::${opts}`;
        },

        addToCart(item) {
            const key = this._key(item);
            const unitPrice = item.subtotalPrice / item.quantity; // 옵션 포함 개당 가격
            const existing = this.cartItems.find(c => c.key === key);
            if (existing) {
                existing.quantity += item.quantity;
            } else {
                this.cartItems.push({ ...item, key, unitPrice });
            }
            this.updateCartTotals();
        },

        removeFromCart(key) {
            this.cartItems = this.cartItems.filter(i => i.key !== key);
            this.updateCartTotals();
        },

        updateCartTotals() {
            this.cartCount = this.cartItems.reduce((s, i) => s + i.quantity, 0);
            this.cartTotal = this.cartItems.reduce((s, i) => s + i.unitPrice * i.quantity, 0);
        },

        setCategory(category) {
            this.currentCategory = category;
        },

        async submitOrder() {
            if (this.isSubmitting) return;
            this.isSubmitting = true;
            try {
                const res = await fetch('/orders', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        qr_token: this.qrToken,
                        cart_items: this.cartItems.map(i => ({
                            item_id: i.itemId,
                            item_name: i.itemName,
                            base_price: i.basePrice,
                            quantity: i.quantity,
                            options: i.options,
                            subtotal_price: i.unitPrice * i.quantity,
                        })),
                        special_instruction: this.specialInstruction,
                    }),
                });
                const html = await res.text();
                if (!res.ok) {
                    // 서버 안내 문구(품절, 주문 중지 등)가 있으면 그대로 보여준다
                    let detail = '';
                    try { detail = JSON.parse(html).detail; } catch (_) { /* HTML 등 */ }
                    alert(typeof detail === 'string' && detail ? detail : '주문에 실패했습니다. 다시 시도해 주세요.');
                    return;
                }

                this.cartItems = [];
                this.updateCartTotals();
                this.showCartModal = false;
                this.specialInstruction = '';
                document.getElementById('modal-container').innerHTML = html;
            } catch (e) {
                console.error(e);
                alert('주문에 실패했습니다. 다시 시도해 주세요.');
            } finally {
                this.isSubmitting = false;
            }
        },
    };
}

function optionData(data) {
    return {
        optionGroups: data.option_groups,
        basePrice: data.basePrice,
        quantity: 1,
        // 단일 선택 그룹은 첫 번째 옵션이 기본 선택 (템플릿의 checked와 같은 값, 서버가 정해 준다)
        selectedOptions: (data.defaultOptions || []).map(o => ({ ...o })),
        showAlert: false,
        selectedRequiredGroupName: '',

        calcItemTotal() {
            const optionsSum = this.selectedOptions.reduce((a, o) => a + o.price, 0);
            return (this.basePrice + optionsSum) * this.quantity;
        },

        addToCartAndClose() {
            for (const group of this.optionGroups) {
                if (group.isRequired && !this.selectedOptions.some(o => o.groupId === group.groupId)) {
                    this.showAlert = true;
                    this.selectedRequiredGroupName = group.groupName;
                    return;
                }
            }
            this.$dispatch('add-to-cart', {
                itemId: data.itemId,
                itemName: data.itemName,
                basePrice: this.basePrice,
                quantity: this.quantity,
                subtotalPrice: this.calcItemTotal(),
                options: this.selectedOptions.map(o => ({ name: o.name, additional_price: o.price })),
            });
            document.getElementById('modal-container').innerHTML = '';
        },
    };
}