// SPDX-License-Identifier: MIT OR Apache-2.0
//! THROWAWAY (deleted at the end of Task 0). Measures what the plan assumes.
use windows::core::{factory, Interface, HSTRING};
use windows::Security::Credentials::UI::{
    UserConsentVerificationResult, UserConsentVerifier, UserConsentVerifierAvailability,
};
use windows::Win32::Foundation::HWND;
use windows::Win32::System::Console::GetConsoleWindow;
use windows::Win32::System::WinRT::IUserConsentVerifierInterop;
use windows::Win32::UI::WindowsAndMessaging::GetForegroundWindow;
use windows_future::{AsyncStatus, IAsyncInfo, IAsyncOperation};

fn main() -> windows::core::Result<()> {
    let which = std::env::args().nth(1).unwrap_or_default();
    let avail = UserConsentVerifier::CheckAvailabilityAsync()?.join()?;
    println!(
        "availability: {avail:?} (Available = {:?})",
        UserConsentVerifierAvailability::Available
    );
    if which == "dpapi" {
        return dpapi_roundtrip();
    }
    let hwnd: HWND = match which.as_str() {
        "console" => unsafe { GetConsoleWindow() },
        _ => unsafe { GetForegroundWindow() },
    };
    println!("owner hwnd ({which}): {:?}", hwnd);
    let interop = factory::<UserConsentVerifier, IUserConsentVerifierInterop>()?;
    let op: IAsyncOperation<UserConsentVerificationResult> = unsafe {
        interop.RequestVerificationForWindowAsync(
            hwnd,
            &HSTRING::from(
                "SPIKE: with-secret test prompt. Approve or cancel - nothing is released.",
            ),
        )?
    };
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(120);
    while op.cast::<IAsyncInfo>()?.Status()? == AsyncStatus::Started {
        if std::time::Instant::now() > deadline {
            op.cast::<IAsyncInfo>()?.Cancel()?;
            println!("timed out, cancelled");
            return Ok(());
        }
        std::thread::sleep(std::time::Duration::from_millis(200));
    }
    println!("result: {:?}", op.GetResults()?);
    Ok(())
}

fn dpapi_roundtrip() -> windows::core::Result<()> {
    use windows::Win32::Foundation::{LocalFree, HLOCAL};
    use windows::Win32::Security::Cryptography::{
        CryptProtectData, CryptUnprotectData, CRYPTPROTECT_UI_FORBIDDEN, CRYPT_INTEGER_BLOB,
    };
    let plain = b"spike-value-123456";
    let entropy = b"overmind.with-secret.v1";
    let mut out = CRYPT_INTEGER_BLOB::default();
    let input = CRYPT_INTEGER_BLOB {
        cbData: plain.len() as u32,
        pbData: plain.as_ptr() as *mut u8,
    };
    let ent = CRYPT_INTEGER_BLOB {
        cbData: entropy.len() as u32,
        pbData: entropy.as_ptr() as *mut u8,
    };
    unsafe {
        CryptProtectData(
            &input,
            None,
            Some(&ent),
            None,
            None,
            CRYPTPROTECT_UI_FORBIDDEN,
            &mut out,
        )?
    };
    let blob = unsafe { std::slice::from_raw_parts(out.pbData, out.cbData as usize).to_vec() };
    unsafe {
        let _ = LocalFree(Some(HLOCAL(out.pbData as _)));
    }
    let mut back = CRYPT_INTEGER_BLOB::default();
    let input = CRYPT_INTEGER_BLOB {
        cbData: blob.len() as u32,
        pbData: blob.as_ptr() as *mut u8,
    };
    unsafe {
        CryptUnprotectData(
            &input,
            None,
            Some(&ent),
            None,
            None,
            CRYPTPROTECT_UI_FORBIDDEN,
            &mut back,
        )?
    };
    let got = unsafe { std::slice::from_raw_parts(back.pbData, back.cbData as usize).to_vec() };
    unsafe {
        let _ = LocalFree(Some(HLOCAL(back.pbData as _)));
    }
    println!(
        "dpapi roundtrip equal: {}  blob != plain: {}",
        got == plain,
        blob != plain
    );
    Ok(())
}
