// SPDX-License-Identifier: MIT OR Apache-2.0
//! Durable pending requests (user-request #4): a request that outlives the
//! process, the reboot and the day, and has NO timeout of its own.
//!
//! What the person sees is unchanged (CireSnave, 2026-10-07, board 134): the
//! Windows Hello prompt alone, showing who asks, which secret and for how
//! long. A pending request is only a record that someone asked; it holds no
//! privilege. Answering it is a NEW prompt through the gate, so a restored
//! request re-prompts and never approves by itself.
//!
//! `bound_hash` binds the request to the artifact the consumer will act on
//! (agentlife: the frozen plan hash). The consumer states the hash it holds
//! now when it answers; any difference voids the request, fail closed.
//! `seal` binds every field of the record to every other, under the store's
//! key, so an altered record is caught at the answer.

use super::*;

/// Most pending requests one role may hold. A request never expires on its
/// own, so the count is what stops a lane filling the file.
pub const MAX_PENDING_PER_ROLE: usize = 20;
/// Most pending requests the store holds.
pub const MAX_PENDING: usize = 100;
/// `bound_hash` is a hash, not a document.
const MAX_BOUND_HASH_CHARS: usize = 128;

/// One request nobody has answered yet.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct PendingRequest {
    /// 128 random bits, hex.
    pub id: String,
    pub created_at: DateTime<Utc>,
    pub request: Request,
    /// What was asked for, as the requester stated it.
    pub grant: Grant,
    /// The artifact this request is bound to (the consumer's own hash).
    pub bound_hash: String,
    /// HMAC over every field above, under the store key.
    pub seal: String,
}

/// What `begin_answer` hands back: show `request` with `grant` through a
/// channel (with the store dropped, as for any prompt), then `resolve`.
#[derive(Clone, Debug, PartialEq)]
pub struct Asking {
    pub pending_id: String,
    pub request: Request,
    pub grant: Grant,
    pub bound_hash: String,
    pub reservation: Reservation,
}

impl Store {
    /// Requests nobody has answered yet.
    pub fn pending(&self) -> &[PendingRequest] {
        &self.pending
    }

    /// Records a request that outlives this process. Refused, before
    /// anything is stored or shown, for a grant over the kind's maximum
    /// (never clamped).
    pub fn submit(
        &mut self,
        req: &Request,
        grant: &Grant,
        bound_hash: &str,
    ) -> Result<String, String> {
        self.submit_at(req, grant, bound_hash, Utc::now())
    }

    pub(crate) fn submit_at(
        &mut self,
        _req: &Request,
        _grant: &Grant,
        _bound_hash: &str,
        _now: DateTime<Utc>,
    ) -> Result<String, String> {
        Err("not built yet".into())
    }

    /// Starts answering a pending request.
    pub fn begin_answer(
        &mut self,
        id: &str,
        bound_hash: &str,
        alert: &dyn Alert,
    ) -> Result<Asking, String> {
        self.begin_answer_at(id, bound_hash, Utc::now(), alert)
    }

    pub(crate) fn begin_answer_at(
        &mut self,
        _id: &str,
        _bound_hash: &str,
        _now: DateTime<Utc>,
        _alert: &dyn Alert,
    ) -> Result<Asking, String> {
        Err("not built yet".into())
    }

    /// The requester gives up on a pending request.
    pub fn withdraw(&mut self, id: &str) -> Result<bool, String> {
        self.withdraw_at(id, Utc::now())
    }

    pub(crate) fn withdraw_at(&mut self, _id: &str, _now: DateTime<Utc>) -> Result<bool, String> {
        Err("not built yet".into())
    }
}

#[cfg(test)]
mod tests;
